"""SMTP submission listener (aiosmtpd `SMTP` protocol + a sandbox handler). INBOUND only.

Nothing here relays: accepted mail is handed to `AgentMailbox` (send / reply / draft+send), so
it becomes simulator state and follows the run's delivery policy; there is no code path to
another mail server (`aiosmtpd.handlers`, which contains a relaying Proxy, is banned).

Session rules
- AUTH PLAIN / LOGIN is required before MAIL (username = mailbox address, password = agent
  API key); credential checks run in a worker thread, never on the event loop. STARTTLS is
  offered when the listener has a TLS context.
- MAIL FROM must be the authenticated mailbox.
- RCPT TO is decided per recipient: simulated mailboxes are accepted; recipients outside the
  simulation are rejected with 550 when the run denies external delivery, else accepted and
  handled by the engine's sink/event policy (never delivered).
- DATA is parsed with `mailsim.mime`; the envelope decides who receives it (header To/Cc that
  are also envelope recipients stay To/Cc, the remaining envelope recipients become Bcc).
  A message whose In-Reply-To (else newest References entry) names a message in the mailbox
  is ONE engine `reply` with the client's recipients/subject/body; unresolvable reply headers
  fall back to draft + send; everything else is an engine `send`.
- Unexpected failures answer `451 4.3.0 internal error` (details are only logged).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Callable
from typing import Any

from aiosmtpd.smtp import MISSING, SMTP, AuthResult, Envelope, Session

from mailsim.domain.errors import SimError
from mailsim.domain.models import CanonicalMessage
from mailsim.engine.context import with_interface
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedMessage, parse_mime
from mailsim.protocols.auth import AgentSession, Authenticator, AuthFailed
from mailsim.protocols.tls import TLSContext

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_RECIPIENTS = 100
COMMAND_CALL_LIMIT = {"AUTH": 3, "*": 100}
INTERNAL_ERROR = "451 4.3.0 internal error"
_SMTP_ERRORS = {
    "EXTERNAL_DELIVERY_BLOCKED": "550 5.7.1",
    "PERMISSION_DENIED": "550 5.7.1",
    "SIMULATION_LIMIT_EXCEEDED": "554 5.3.4",
    "RATE_LIMITED": "451 4.7.1",
    "RUN_NOT_ACTIVE": "554 5.3.2",
    "INVALID_REQUEST": "554 5.6.0",
    "NOT_FOUND": "554 5.1.0",
    "UNSUPPORTED_OPERATION": "554 5.5.1",
    "CONFLICT": "451 4.3.0",
}


def _line(text: str) -> str:
    return " ".join(text.split())[:400]


def smtp_error(exc: SimError) -> str:
    prefix = _SMTP_ERRORS.get(str(exc.code), "451 4.3.0")
    return f"{prefix} {exc.code}: {_line(exc.message)}"


def _valid_address(addr: str) -> bool:
    local, _, domain = addr.rpartition("@")
    return bool(local) and bool(domain) and not any(c.isspace() or c in "<>()," for c in addr)


class SandboxHandler:
    """aiosmtpd handler: commits accepted mail to the simulator, never relays."""

    def __init__(self, authenticator: Authenticator, *, max_recipients: int | None = None,
                 limits: MimeLimits | None = None) -> None:  # fmt: skip
        self.auth = authenticator
        self.max_recipients = max_recipients or MAX_RECIPIENTS
        self.limits = limits or MimeLimits(max_message_bytes=MAX_MESSAGE_BYTES)

    # ------------------------------------------------------------------ AUTH (off the loop)
    async def auth_PLAIN(self, server: SMTP, args: list[str]) -> AuthResult:
        if len(args) == 1:
            blob = await server.challenge_auth("")
            if blob is MISSING:
                return AuthResult(success=False)
        else:
            try:
                blob = base64.b64decode(args[1].encode(), validate=True)
            except (binascii.Error, ValueError):
                await server.push("501 5.5.2 Can't decode base64")
                return AuthResult(success=False)
        assert isinstance(blob, bytes)
        parts = blob.split(b"\0")
        if len(parts) != 3:
            await server.push("501 5.5.2 Can't split auth value")
            return AuthResult(success=False)
        if parts[0] and parts[0] != parts[1]:  # no proxy authorization (parity with IMAP)
            return AuthResult(success=False, handled=False)
        return await self._login(parts[1], parts[2])

    async def auth_LOGIN(self, server: SMTP, args: list[str]) -> AuthResult:
        if len(args) == 1:
            login = await server.challenge_auth(server.AuthLoginUsernameChallenge)
        else:
            try:
                login = base64.b64decode(args[1].encode(), validate=True)
            except (binascii.Error, ValueError):
                await server.push("501 5.5.2 Can't decode base64")
                return AuthResult(success=False)
        if login is MISSING:
            return AuthResult(success=False)
        password = await server.challenge_auth(server.AuthLoginPasswordChallenge)
        if password is MISSING:
            return AuthResult(success=False)
        assert isinstance(login, bytes) and isinstance(password, bytes)
        return await self._login(login, password)

    async def _login(self, login: bytes, password: bytes) -> AuthResult:
        try:
            agent = await asyncio.to_thread(with_interface("smtp", self.auth.login),
                                            login.decode("utf-8", "replace"),
                                            password.decode("utf-8", "replace"))  # fmt: skip
        except AuthFailed as exc:
            log.info("SMTP AUTH failed: %s", exc.reason)
            if exc.code is not None:  # valid key, but the engine refused this mailbox / run
                return AuthResult(success=False, handled=False,
                                  message=f"535 5.7.8 {_line(exc.reason)}")  # fmt: skip
            return AuthResult(success=False, handled=False)
        return AuthResult(success=True, auth_data=agent)

    async def handle_exception(self, error: Exception) -> str:
        log.error("SMTP session error", exc_info=error)
        return INTERNAL_ERROR

    # ------------------------------------------------------------------ transaction
    async def _live(self, server: SMTP, session: Session) -> AgentSession | str:
        agent = session.auth_data
        if not isinstance(agent, AgentSession):
            return "530 5.7.0 Authentication required"
        try:
            valid = await asyncio.to_thread(self.auth.still_valid, agent)
        except Exception:
            log.exception("SMTP credential re-check failed")
            valid = False
        if not valid:
            assert server.transport is not None  # 421 is sent first, then the connection closes
            asyncio.get_running_loop().call_soon(server.transport.close)
            return "421 4.7.0 credentials revoked or expired; closing connection"
        return agent

    async def handle_MAIL(self, server: SMTP, session: Session, envelope: Envelope,
                          address: str, mail_options: list[str]) -> str:  # fmt: skip
        agent = await self._live(server, session)
        if isinstance(agent, str):
            return agent
        if address.strip().lower() != agent.address:
            return (f"553 5.7.1 sender <{_line(address)[:200]}> is not the authenticated "
                    f"mailbox <{agent.address}>")  # fmt: skip
        agent.policy = None  # recipient rules are re-read per transaction (survive run resets)
        envelope.mail_from = address
        envelope.mail_options.extend(mail_options)
        return "250 2.1.0 OK"

    async def handle_RCPT(self, server: SMTP, session: Session, envelope: Envelope,
                          address: str, rcpt_options: list[str]) -> str:  # fmt: skip
        agent = await self._live(server, session)
        if isinstance(agent, str):
            return agent
        addr = address.strip().lower()
        if not _valid_address(addr):
            return "501 5.1.3 bad recipient address syntax"
        if len(envelope.rcpt_tos) >= self.max_recipients:
            return "452 4.5.3 too many recipients"
        policy = await asyncio.to_thread(self.auth.recipient_policy, agent)
        if not policy.allows(addr):
            return (f"550 5.7.1 <{addr}> EXTERNAL_DELIVERY_BLOCKED: recipient is outside the "
                    "simulated environment and external delivery is not permitted")  # fmt: skip
        envelope.rcpt_tos.append(addr)
        envelope.rcpt_options.extend(rcpt_options)
        return "250 2.1.5 OK"

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        agent = await self._live(server, session)
        if isinstance(agent, str):
            return agent
        raw = envelope.original_content or b""
        rcpts = [str(r).lower() for r in envelope.rcpt_tos]
        return await asyncio.to_thread(with_interface("smtp", self.deliver), agent, raw, rcpts)

    def deliver(self, agent: AgentSession, raw: bytes, rcpts: list[str]) -> str:
        try:
            parsed = parse_mime(raw, self.limits)
            if parsed.attachments:
                return ("554 5.6.1 UNSUPPORTED_OPERATION: attachments cannot be submitted over "
                        "SMTP in this simulator (the agent send operations carry text/html "
                        "only)")  # fmt: skip
            result = self._submit(agent, parsed, rcpts)
        except SimError as exc:
            return smtp_error(exc)
        except Exception:
            log.exception("SMTP delivery failed")
            return INTERNAL_ERROR
        return f"250 2.0.0 OK queued as {result['message_id_header']} id={result['message_id']}"

    def _submit(self, agent: AgentSession, p: ParsedMessage, rcpts: list[str]) -> dict[str, Any]:
        mb = agent.mailbox
        envelope = list(dict.fromkeys(rcpts))
        to = [a for a in dict.fromkeys(x.email for x in p.to) if a in envelope]
        cc = [a for a in dict.fromkeys(x.email for x in p.cc) if a in envelope and a not in to]
        bcc = [a for a in envelope if a not in to and a not in cc]
        text = p.body_text or ""
        orig = self._original(agent, p)
        if orig is not None:
            return dict(mb.reply(orig.id, text=text, to=to, cc=cc, bcc=bcc, subject=p.subject,
                                 html=p.body_html))  # fmt: skip
        if p.in_reply_to or p.references:
            draft = mb.create_draft(to=to, subject=p.subject, text=text, cc=cc, bcc=bcc,
                                    html=p.body_html, in_reply_to=p.in_reply_to,
                                    references=p.references or None)  # fmt: skip
            return dict(mb.send_draft(str(draft["message_id"])))
        return dict(mb.send(to=to, subject=p.subject, text=text, cc=cc, bcc=bcc, html=p.body_html))

    @staticmethod
    def _original(agent: AgentSession, p: ParsedMessage) -> CanonicalMessage | None:
        """The message being answered: In-Reply-To, else References newest -> oldest."""
        candidates = [p.in_reply_to] if p.in_reply_to else list(reversed(p.references))
        for header in candidates:
            found = agent.mailbox.find_by_message_id(header) if header else []
            if found:
                return found[0]
        return None


class _TrackedSMTP(SMTP):
    """Reports connections to the listener (shutdown, connection cap)."""

    on_connect: Callable[[SMTP], bool] = staticmethod(lambda protocol: True)
    on_disconnect: Callable[[SMTP], None] = staticmethod(lambda protocol: None)
    _rejected = False
    _counted = False  # STARTTLS calls connection_made again: count each connection once

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not self._counted:
            self._counted = True
            if not self.on_connect(self):
                self._rejected = True
                assert isinstance(transport, asyncio.WriteTransport)
                transport.write(b"421 4.7.0 too many connections, try again later\r\n")
                transport.close()
                return
        super().connection_made(transport)

    def connection_lost(self, error: Exception | None) -> None:
        if self._rejected:
            return
        self.on_disconnect(self)
        super().connection_lost(error)

    def close_transport(self) -> None:
        transport = self._original_transport or self.transport
        assert transport is not None  # sessions leave the listener's set when they disconnect
        transport.close()

    def eof_received(self) -> bool | None:
        return None if self._rejected else super().eof_received()


def smtp_factory(authenticator: Authenticator, *, tls_context: TLSContext | None = None,
                 hostname: str = "localhost",
                 on_connect: Callable[[SMTP], bool] | None = None,
                 on_disconnect: Callable[[SMTP], None] | None = None,
                 ) -> Callable[[], SMTP]:  # fmt: skip
    """Protocol factory for `loop.create_server`."""
    handler = SandboxHandler(authenticator)

    def factory() -> SMTP:
        server = _TrackedSMTP(
            handler,
            hostname=hostname,
            ident="mailsim sandbox ESMTP (no relay)",
            data_size_limit=MAX_MESSAGE_BYTES,
            decode_data=False,
            tls_context=tls_context,
            require_starttls=False,
            # AUTH is enforced in handle_MAIL (aiosmtpd's own flag warns on every connection
            # when TLS is optional); TLS is optional on the loopback sandbox
            auth_required=False,
            auth_require_tls=False,
            command_call_limit=COMMAND_CALL_LIMIT,
        )
        server.on_connect = on_connect or _TrackedSMTP.on_connect
        server.on_disconnect = on_disconnect or _TrackedSMTP.on_disconnect
        return server

    return factory


__all__ = ["SandboxHandler", "smtp_error", "smtp_factory"]
