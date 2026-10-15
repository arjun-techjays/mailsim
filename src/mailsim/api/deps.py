"""Request dependencies. Frozen signatures; WS-A implements credential verification.

Every route declares exactly one role dependency (enforced by a contract test):
- `require_role(...)` for control-plane / simulator / evaluation routes
- `agent_for_mailbox` for agent mailbox routes (agent role, bound run + path mailbox)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import PermissionDenied, UnsupportedOperation

bearer = HTTPBearer(auto_error=False, description="Sandbox-issued API key (role-scoped)")
OPERATOR_ROLES = frozenset({Role.CONTROLLER, Role.EVALUATOR})


def _unauthenticated(message: str) -> HTTPException:
    # mapped to the frozen envelope with code UNAUTHENTICATED (api/errors.py)
    return HTTPException(status_code=401, detail=message, headers={"WWW-Authenticate": "Bearer"})


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    """Resolve the caller's Principal from a sandbox API key (`Authorization: Bearer msk_...`).

    Missing, malformed, unknown, expired and revoked keys all answer 401 with the same
    message, so the response never reveals whether a key exists.
    """
    operator: Principal | None = getattr(request.app.state, "local_operator", None)
    if credentials is None and operator is not None:
        # local no-auth mode (loopback only, opt-in): key-less requests are the operator
        request.state.principal = operator
        return operator
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated("missing or invalid API key")
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise not_implemented("server configuration: no session_factory")
    with session_factory() as session:
        try:
            principal = verify_key(session, credentials.credentials)
        except InvalidCredential:
            raise _unauthenticated("missing or invalid API key") from None
    request.state.principal = principal
    return principal


def require_role(*roles: Role) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.local_operator and allowed & OPERATOR_ROLES:
            return principal
        if principal.role not in allowed:
            raise PermissionDenied("this credential's role may not call this operation")
        return principal

    dependency.mailsim_roles = allowed  # type: ignore[attr-defined]  # introspected by tests
    return dependency


def agent_for_mailbox(
    mailbox_id: str, principal: Annotated[Principal, Depends(get_principal)]
) -> Principal:
    """Agent credentials only, and only for the mailbox (and run) they were issued for.
    The local no-auth operator is never an agent: agent interfaces always demand a key."""
    if principal.local_operator:
        raise _unauthenticated("agent interfaces require an agent API key")
    if principal.role != Role.AGENT or principal.mailbox_id != mailbox_id:
        raise PermissionDenied("agent credentials are bound to one mailbox")
    return principal


agent_for_mailbox.mailsim_roles = frozenset({Role.AGENT})  # type: ignore[attr-defined]

Controller = Annotated[Principal, Depends(require_role(Role.CONTROLLER))]
Evaluator = Annotated[Principal, Depends(require_role(Role.EVALUATOR))]
Inspector = Annotated[Principal, Depends(require_role(Role.CONTROLLER, Role.EVALUATOR))]
# any valid credential (identity introspection only: GET /v1/me)
Authenticated = Annotated[Principal, Depends(require_role(*Role))]
Agent = Annotated[Principal, Depends(agent_for_mailbox)]


def not_implemented(owner: str) -> UnsupportedOperation:
    return UnsupportedOperation(f"not implemented yet (owner: {owner})", details={"owner": owner})
