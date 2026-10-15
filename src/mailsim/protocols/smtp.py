"""SMTP submission listener (aiosmtpd `SMTP` protocol + a sandbox handler). INBOUND only.

Nothing here relays: accepted mail is handed to `AgentMailbox` (send / reply / draft+send), so
it becomes simulator state and follows the run's delivery policy; there is no code path to
another mail server (`aiosmtpd.handlers`, which contains a relaying Proxy, is banned).

Session rules
- AUTH PLAIN / LOGIN is required before MAIL (username = mailbox address, password = agent
  API key). STARTTLS is offered when the listener has a TLS context.
- MAIL FROM must be the authenticated mailbox.
- RCPT TO is decided per recipient: simulated mailboxes are accepted; recipients outside the
  simulation are rejected with 550 when the run denies external delivery (deny mode, or the
  agent is prohibited from `external_delivery`), else accepted and handled by the engine's
  sink/event policy (never delivered).
- DATA is parsed with `mailsim.mime`; the envelope decides who receives it (header To/Cc that
  are also envelope recipients stay To/Cc, the remaining envelope recipients become Bcc).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiosmtpd.smtp import SMTP, AuthResult, Envelope, LoginPassword, Session

from mailsim.domain.errors import SimError
from mailsim.domain.models import CanonicalMessage
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedMessage, parse_mime
from mailsim.protocols.auth import AgentSession, Authenticator, AuthFailed
from mailsim.protocols.tls import TLSContext

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_RECIPIENTS = 100
PAGE_SIZE = 500  # engine maximum
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


def reply_all_mode(orig: CanonicalMessage, me: str, recipients: set[str]) -> bool | None:
    """Mirror the engine's reply recipient rules: True/False when `recipients` is exactly what
    `reply(reply_all=...)` would address, else None."""
    sender = orig.from_.email
    plain = [sender] if sender != me else [a.email for a in orig.to]
    every = plain + [a.email for a in orig.to if a.email != me]
    cc = [a.email for a in orig.cc if a.email != me]

    def final(to: list[str], cc_: list[str]) -> set[str]:
        return set([a for a in dict.fromkeys(to) if a != me] or [sender]) | set(cc_)

    if recipients == final(plain, []):
        return False
    if recipients == final(every, cc):
        return True
    return None


class SandboxHandler:
    """aiosmtpd handler: commits accepted mail to the simulator, never relays."""

    def __init__(self, authenticator: Authenticator, *, max_recipients: int | None = None,
                 limits: MimeLimits | None = None) -> None:  # fmt: skip
        self.auth = authenticator
        self.max_recipients = max_recipients or MAX_RECIPIENTS
        self.limits = limits or MimeLimits(max_message_bytes=MAX_MESSAGE_BYTES)

    # aiosmtpd `authenticator` callable (runs inside the AUTH command)
    def authenticate(self, server: SMTP, session: Session, envelope: Envelope, mechanism: str,
                     auth_data: Any) -> AuthResult:  # fmt: skip
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)
        try:
            agent = self.auth.login(auth_data.login.decode("utf-8", "replace"),
                                    auth_data.password.decode("utf-8", "replace"))  # fmt: skip
        except AuthFailed as exc:
            log.info("SMTP AUTH failed: %s", exc.reason)
            if exc.code is not None:  # valid key, but the engine refused this mailbox / run
                return AuthResult(success=False, handled=False,
                                  message=f"535 5.7.8 {_line(exc.reason)}")  # fmt: skip
            return AuthResult(success=False, handled=False)
        return AuthResult(success=True, auth_data=agent)

    @staticmethod
    def _agent(session: Session) -> AgentSession:
        agent = session.auth_data
        assert isinstance(agent, AgentSession)  # MAIL requires AUTH; RCPT/DATA require MAIL
        return agent

    async def handle_MAIL(self, server: SMTP, session: Session, envelope: Envelope,
                          address: str, mail_options: list[str]) -> str:  # fmt: skip
        if not isinstance(session.auth_data, AgentSession):
            return "530 5.7.0 Authentication required"
        agent = self._agent(session)
        if address.strip().lower() != agent.address:
            return (f"553 5.7.1 sender <{_line(address)[:200]}> is not the authenticated "
                    f"mailbox <{agent.address}>")  # fmt: skip
        envelope.mail_from = address
        envelope.mail_options.extend(mail_options)
        return "250 2.1.0 OK"

    async def handle_RCPT(self, server: SMTP, session: Session, envelope: Envelope,
                          address: str, rcpt_options: list[str]) -> str:  # fmt: skip
        agent = self._agent(session)
        addr = address.strip().lower()
        if not _valid_address(addr):
            return "501 5.1.3 bad recipient address syntax"
        if len(envelope.rcpt_tos) >= self.max_recipients:
            return "452 4.5.3 too many recipients"
        policy = await asyncio.to_thread(self.auth.recipient_policy, agent)
        if policy.is_internal(addr):
            envelope.rcpt_tos.append(addr)
            envelope.rcpt_options.extend(rcpt_options)
            return "250 2.1.5 OK"
        if policy.external_allowed:
            envelope.rcpt_tos.append(addr)
            envelope.rcpt_options.extend(rcpt_options)
            return (f"250 2.1.5 OK (outside the simulation: handled by the {policy.mode!r} "
                    "delivery policy, never delivered)")  # fmt: skip
        return (f"550 5.7.1 <{addr}> EXTERNAL_DELIVERY_BLOCKED: recipient is outside the "
                "simulated environment and the scenario's delivery policy denies external "
                "delivery")  # fmt: skip

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        agent = self._agent(session)
        raw = envelope.original_content or b""
        rcpts = [str(r).lower() for r in envelope.rcpt_tos]
        return await asyncio.to_thread(self.deliver, agent, raw, rcpts)

    def deliver(self, agent: AgentSession, raw: bytes, rcpts: list[str]) -> str:
        try:
            parsed = parse_mime(raw, self.limits)
        except SimError as exc:
            return smtp_error(exc)
        if parsed.attachments:
            return ("554 5.6.1 UNSUPPORTED_OPERATION: attachments cannot be submitted over "
                    "SMTP in this simulator (the agent send operation carries text/html only)")  # fmt: skip
        try:
            result = self._submit(agent, parsed, rcpts)
        except SimError as exc:
            return smtp_error(exc)
        return f"250 2.0.0 OK queued as {result['message_id_header']} id={result['message_id']}"

    def _submit(self, agent: AgentSession, p: ParsedMessage, rcpts: list[str]) -> dict[str, Any]:
        mb = agent.mailbox
        envelope = list(dict.fromkeys(rcpts))
        to = [a for a in dict.fromkeys(x.email for x in p.to) if a in envelope]
        cc = [a for a in dict.fromkeys(x.email for x in p.cc) if a in envelope and a not in to]
        bcc = [a for a in envelope if a not in to and a not in cc]
        text = p.body_text or ""
        if p.in_reply_to:
            orig = self._original(agent, p.in_reply_to)
            mode = reply_all_mode(orig, agent.address, set(envelope)) if orig else None
            if orig is not None and mode is not None and p.body_html is None:
                return dict(mb.reply(orig.id, text=text, reply_all=mode))
        if p.in_reply_to or p.references:
            draft = mb.create_draft(to=to, subject=p.subject, text=text, cc=cc, bcc=bcc,
                                    html=p.body_html, in_reply_to=p.in_reply_to,
                                    references=p.references or None)  # fmt: skip
            return dict(mb.send_draft(str(draft["message_id"])))
        return dict(mb.send(to=to, subject=p.subject, text=text, cc=cc, bcc=bcc, html=p.body_html))

    @staticmethod
    def _original(agent: AgentSession, header: str) -> CanonicalMessage | None:
        cursor: str | None = None
        while True:
            page = agent.mailbox.list_messages(page_size=PAGE_SIZE, cursor=cursor)
            for msg in page.items:
                if msg.message_id_header == header:
                    return msg
            cursor = page.next_cursor
            if cursor is None:
                return None


class _TrackedSMTP(SMTP):
    """Reports each connection's transport so the listener can close it on shutdown."""

    on_connect: Callable[[asyncio.BaseTransport], None] = staticmethod(lambda transport: None)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        self.on_connect(transport)


def smtp_factory(authenticator: Authenticator, *, tls_context: TLSContext | None = None,
                 hostname: str = "localhost",
                 on_connect: Callable[[asyncio.BaseTransport], None] | None = None,
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
            authenticator=handler.authenticate,
        )
        server.on_connect = on_connect or _TrackedSMTP.on_connect
        return server

    return factory


__all__ = ["SandboxHandler", "reply_all_mode", "smtp_error", "smtp_factory"]
