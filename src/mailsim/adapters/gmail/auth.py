"""Credentials for the Gmail adapter: ALWAYS an agent key (``Authorization: Bearer msk_...``).

This deliberately does not reuse the REST dependency: any local no-auth / operator mode the
control plane may offer never applies here. The key must verify, carry the agent role and be
bound to a run and a mailbox; the engine facade built from it is the only engine access.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import Depends, Request

from mailsim.adapters.gmail.errors import GmailAuthError
from mailsim.adapters.gmail.labels import LabelRegistry
from mailsim.adapters.gmail.service import GmailService
from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import PermissionDenied, UnsupportedOperation
from mailsim.engine.facades import AgentMailbox, facade_for

REGISTRY_ATTR = "gmail_label_registry"
_REGISTRY_LOCK = threading.Lock()


def _state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise UnsupportedOperation(
            f"server is not configured with {name}", details={"missing": name}
        )
    return value


def agent_principal(request: Request) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise GmailAuthError
    with _state(request, "session_factory")() as session:
        try:
            principal = verify_key(session, token.strip())
        except InvalidCredential:
            raise GmailAuthError from None
    if principal.role != Role.AGENT or not principal.run_id or not principal.mailbox_id:
        raise PermissionDenied("the Gmail API accepts agent credentials only")
    request.state.principal = principal
    return principal


def _registry(request: Request) -> LabelRegistry:
    with _REGISTRY_LOCK:
        registry = getattr(request.app.state, REGISTRY_ATTR, None)
        if registry is None:
            registry = LabelRegistry()
            setattr(request.app.state, REGISTRY_ATTR, registry)
        return registry


def gmail_service(
    request: Request, user_id: str, principal: Annotated[Principal, Depends(agent_principal)]
) -> GmailService:
    mailbox = facade_for(_state(request, "engine"), principal)
    assert isinstance(mailbox, AgentMailbox)  # agent role checked above
    service = GmailService(mailbox, principal, _state(request, "session_factory"),
                           _state(request, "blobs"), _registry(request))  # fmt: skip
    service.check_user(user_id)
    return service


Gmail = Annotated[GmailService, Depends(gmail_service)]
Agent = Annotated[Principal, Depends(agent_principal)]
