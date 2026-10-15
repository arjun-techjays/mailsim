"""Sandbox credential checks shared by the IMAP and SMTP listeners.

username = the mailbox address, password = an AGENT API key bound to that run + mailbox.
There is no anonymous or local no-auth mode here (MAILSIM_LOCAL_NO_AUTH is a dashboard /
control-plane setting and is deliberately ignored): every protocol session is an agent session
and every mailbox operation goes through `AgentMailbox`, so the engine's permission, mailbox
and budget guards apply exactly as they do over REST.

Sessions re-verify their key at most every `REVERIFY_SECONDS`, so a revoked or expired key, or
a run that reached a terminal state, ends live IMAP/SMTP sessions too.

One read-only lookup goes to the store directly because `AgentMailbox` does not expose it
(reported to the integrator): the run's internal addresses and external-delivery decision, to
answer SMTP RCPT per recipient. It is reduced to allow/deny and never reaches scenario content
or answer keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import SimError
from mailsim.engine.facades import AgentMailbox
from mailsim.store import models as m
from mailsim.store.repos import Scope

REVERIFY_SECONDS = 5.0


class AuthFailed(Exception):
    """Credentials rejected. `reason` is for logs only unless `code` is set."""

    def __init__(self, reason: str, *, code: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # set when the credential is valid but the ENGINE refused the session (the run is not
        # active, the mailbox is not the agent's); only then is the reason shown to the client
        self.code = code


@dataclass(frozen=True)
class RecipientPolicy:
    internal: frozenset[str]  # addresses of simulated mailboxes in the run
    external_allowed: bool  # sink/event mode and the agent may deliver externally

    def allows(self, address: str) -> bool:
        return self.external_allowed or address.strip().lower() in self.internal


@dataclass(eq=False)
class AgentSession:
    mailbox: AgentMailbox
    address: str  # lower-cased mailbox address (the only valid MAIL FROM / LOGIN name)
    key: str = field(default="", repr=False)
    verified_at: float = 0.0
    policy: RecipientPolicy | None = None  # cached per session


class Authenticator:
    """Resolves (username, API key) to an `AgentSession`."""

    def __init__(self, engine: Any, session_factory: sessionmaker[Session] | None = None,
                 *, reverify_seconds: float = REVERIFY_SECONDS) -> None:  # fmt: skip
        self.engine = engine
        self.session_factory: sessionmaker[Session] = session_factory or engine.session_factory
        self.reverify_seconds = reverify_seconds

    def _principal(self, key: str) -> Principal:
        with self.session_factory() as s:
            try:
                return verify_key(s, key.strip())
            except InvalidCredential as exc:
                raise AuthFailed(str(exc)) from None

    def login(self, username: str, password: str) -> AgentSession:
        principal = self._principal(password)
        if principal.role != Role.AGENT or not principal.run_id or not principal.mailbox_id:
            raise AuthFailed("protocol listeners accept agent credentials only")
        mailbox = AgentMailbox(self.engine, Scope(principal.tenant_id, principal.project_id),
                               principal.run_id, principal.mailbox_id)  # fmt: skip
        try:  # the engine decides: run active, mailbox is the agent's, ... (an audited read)
            info = mailbox.mailbox_info()
        except SimError as exc:
            raise AuthFailed(f"{exc.code}: {exc.message}", code=str(exc.code)) from None
        address = str(info["email"]).lower()
        if username.strip().lower() != address:
            raise AuthFailed("username does not match the key's mailbox")
        return AgentSession(mailbox, address, password.strip(), time.monotonic())

    def still_valid(self, agent: AgentSession) -> bool:
        """False once the key is revoked/expired or its run ended (checked at most every
        `reverify_seconds`)."""
        now = time.monotonic()
        if now - agent.verified_at < self.reverify_seconds:
            return True
        try:
            self._principal(agent.key)
        except AuthFailed:
            return False
        agent.verified_at = now
        return True

    def recipient_policy(self, agent: AgentSession) -> RecipientPolicy:
        if agent.policy is not None:
            return agent.policy
        run_id = agent.mailbox.run_id
        with self.session_factory() as s:
            emails = s.execute(select(m.Mailbox.email).where(m.Mailbox.run_id == run_id)).scalars()
            internal = frozenset(e.lower() for e in emails)
            run = s.get(m.Run, run_id)
            config: dict[str, Any] = dict(run.config) if run is not None else {}
        mode = str(config.get("external_delivery", "deny"))
        prohibited = (config.get("agent") or {}).get("prohibited") or []
        agent.policy = RecipientPolicy(
            internal, mode != "deny" and "external_delivery" not in prohibited
        )
        return agent.policy


__all__ = ["AgentSession", "AuthFailed", "Authenticator", "RecipientPolicy"]
