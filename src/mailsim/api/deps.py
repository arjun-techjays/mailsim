"""Request dependencies. Frozen signatures; WS-A implements credential verification.

Every route declares exactly one role dependency (enforced by a contract test):
- `require_role(...)` for control-plane / simulator / evaluation routes
- `agent_for_mailbox` for agent mailbox routes (agent role, bound run + path mailbox)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import PermissionDenied, UnsupportedOperation

bearer = HTTPBearer(auto_error=False, description="Sandbox-issued API key (role-scoped)")


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    """Resolve the caller's Principal from a sandbox API key. Implemented by WS-A."""
    raise not_implemented("WS-A")


def require_role(*roles: Role) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise PermissionDenied("this credential's role may not call this operation")
        return principal

    dependency.mailsim_roles = allowed  # type: ignore[attr-defined]  # introspected by tests
    return dependency


def agent_for_mailbox(
    mailbox_id: str, principal: Annotated[Principal, Depends(get_principal)]
) -> Principal:
    """Agent credentials only, and only for the mailbox (and run) they were issued for."""
    if principal.role != Role.AGENT or principal.mailbox_id != mailbox_id:
        raise PermissionDenied("agent credentials are bound to one mailbox")
    return principal


agent_for_mailbox.mailsim_roles = frozenset({Role.AGENT})  # type: ignore[attr-defined]

Controller = Annotated[Principal, Depends(require_role(Role.CONTROLLER))]
Evaluator = Annotated[Principal, Depends(require_role(Role.EVALUATOR))]
Inspector = Annotated[Principal, Depends(require_role(Role.CONTROLLER, Role.EVALUATOR))]
Agent = Annotated[Principal, Depends(agent_for_mailbox)]


def not_implemented(owner: str) -> UnsupportedOperation:
    return UnsupportedOperation(f"not implemented yet (owner: {owner})", details={"owner": owner})
