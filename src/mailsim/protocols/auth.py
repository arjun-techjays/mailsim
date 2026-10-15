"""Sandbox credential checks shared by the IMAP and SMTP listeners.

username = the mailbox address, password = an AGENT API key bound to that run + mailbox.
There is no anonymous or local no-auth mode here (MAILSIM_LOCAL_NO_AUTH is a dashboard /
control-plane setting and is deliberately ignored): every protocol session is an agent session
and every mailbox operation goes through `AgentMailbox`, so the engine's permission, mailbox
and budget guards apply exactly as they do over REST.

Two small read-only lookups go to the store directly because `AgentMailbox` does not expose
them (reported to the integrator): the bound mailbox's address (to match the username) and the
run's external-delivery policy (to answer SMTP RCPT per recipient). Neither reaches scenario
content or answer keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.auth.principal import Role
from mailsim.domain.errors import SimError
from mailsim.engine.facades import AgentMailbox
from mailsim.store import models as m
from mailsim.store.repos import Scope


class AuthFailed(Exception):
    """Credentials rejected. `reason` is for logs only; clients get a generic message."""

    def __init__(self, reason: str, *, code: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # set when the credential is valid but the ENGINE refused the session (the run is not
        # active, the mailbox is not the agent's); only then is the reason shown to the client
        self.code = code


@dataclass(frozen=True)
class AgentSession:
    mailbox: AgentMailbox
    address: str  # lower-cased mailbox address (the only valid MAIL FROM / LOGIN name)


@dataclass(frozen=True)
class RecipientPolicy:
    internal: frozenset[str]  # addresses of simulated mailboxes in the run
    external_allowed: bool  # sink/event mode and the agent may deliver externally
    mode: str  # deny | sink | event

    def is_internal(self, address: str) -> bool:
        return address.strip().lower() in self.internal


class Authenticator:
    """Resolves (username, API key) to an `AgentSession`."""

    def __init__(self, engine: Any, session_factory: sessionmaker[Session] | None = None) -> None:
        self.engine = engine
        self.session_factory: sessionmaker[Session] = session_factory or engine.session_factory

    def login(self, username: str, password: str) -> AgentSession:
        with self.session_factory() as s:
            try:
                principal = verify_key(s, password.strip())
            except InvalidCredential as exc:
                raise AuthFailed(str(exc)) from None
            if principal.role != Role.AGENT or not principal.run_id or not principal.mailbox_id:
                raise AuthFailed("protocol listeners accept agent credentials only")
            row = s.get(m.Mailbox, (principal.mailbox_id, principal.run_id))
            if row is None:  # tenant scoping is enforced by the engine on every call
                raise AuthFailed("the key's mailbox does not exist")
            address = row.email.lower()
        if username.strip().lower() != address:
            raise AuthFailed("username does not match the key's mailbox")
        mailbox = AgentMailbox(self.engine, Scope(principal.tenant_id, principal.project_id),
                               principal.run_id, principal.mailbox_id)  # fmt: skip
        try:  # the engine decides: run active, mailbox is the agent's, ... (an audited read)
            mailbox.list_folders()
        except SimError as exc:
            raise AuthFailed(f"{exc.code}: {exc.message}", code=str(exc.code)) from None
        return AgentSession(mailbox, address)

    def recipient_policy(self, agent: AgentSession) -> RecipientPolicy:
        run_id = agent.mailbox.run_id
        with self.session_factory() as s:
            emails = s.execute(select(m.Mailbox.email).where(m.Mailbox.run_id == run_id)).scalars()
            internal = frozenset(e.lower() for e in emails)
            run = s.get(m.Run, run_id)
            config: dict[str, Any] = dict(run.config) if run is not None else {}
        mode = str(config.get("external_delivery", "deny"))
        prohibited = (config.get("agent") or {}).get("prohibited") or []
        return RecipientPolicy(internal, mode != "deny" and "external_delivery" not in prohibited,
                               mode)  # fmt: skip


__all__ = ["AgentSession", "AuthFailed", "Authenticator", "RecipientPolicy"]
