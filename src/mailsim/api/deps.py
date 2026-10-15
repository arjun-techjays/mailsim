"""Request dependencies. Frozen signatures; WS-A implements credential verification."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mailsim.auth.principal import Principal
from mailsim.domain.errors import UnsupportedOperation

bearer = HTTPBearer(auto_error=False, description="Sandbox-issued API key (role-scoped)")


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    """Resolve the caller's Principal from a sandbox API key. Implemented by WS-A."""
    raise not_implemented("WS-A")


def not_implemented(owner: str) -> UnsupportedOperation:
    return UnsupportedOperation(f"not implemented yet (owner: {owner})", details={"owner": owner})
