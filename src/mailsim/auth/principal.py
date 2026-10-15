"""Authenticated caller identity. Frozen contract for REST, adapters, protocols and MCP.

Each credential has exactly one role (spec 8.4). Agent credentials are bound to one run and
one mailbox; controller/evaluator credentials to a tenant + project.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mailsim.domain.errors import PermissionDenied


class Role(StrEnum):
    AGENT = "agent"
    CONTROLLER = "controller"
    EVALUATOR = "evaluator"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    project_id: str
    role: Role
    run_id: str | None = None
    mailbox_id: str | None = None
    key_id: str | None = None
    # Local no-auth mode only (loopback-bound dev server): the key-less operator acts as
    # controller AND evaluator for one project. Never valid for agent interfaces.
    local_operator: bool = False

    def __post_init__(self) -> None:
        # Roles usually arrive as strings (DB columns, tokens): coerce, and reject unknowns.
        try:
            object.__setattr__(self, "role", Role(self.role))
        except ValueError:
            raise PermissionDenied(f"unknown role {self.role!r}") from None
