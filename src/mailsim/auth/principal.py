"""Authenticated caller identity. Frozen contract for REST, adapters, protocols and MCP.

Each credential has exactly one role (spec 8.4). Agent credentials are bound to one run and
one mailbox; controller/evaluator credentials to a tenant + project.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
