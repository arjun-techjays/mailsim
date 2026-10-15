"""Sandbox API keys: issue and verify (spec 5.6, 8.4, 10 "credential isolation").

Format: ``msk_<prefix>_<secret>``. ``prefix`` (12 hex chars) is stored in clear to find the
row; only the sha256 of the FULL key is stored, and it is compared in constant time. Keys are
role-scoped: controller/evaluator keys bind a tenant + project, agent keys additionally bind
one run + one mailbox. A key is shown exactly once, when it is issued.

Lives in ``mailsim.api`` (not ``mailsim.auth``) because the layering contract forbids
``mailsim.auth`` from importing ``mailsim.store``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.ids import new_control_id
from mailsim.store import models as m
from mailsim.store import repos

KEY_PREFIX = "msk"
_KEY_RE = re.compile(r"^msk_([0-9a-f]{12})_([A-Za-z0-9_-]{32,128})$")
MAX_KEY_CHARS = 200


class InvalidCredential(Exception):
    """Missing, malformed, unknown, expired or revoked key (HTTP 401, never more detail)."""


@dataclass(frozen=True)
class IssuedKey:
    api_key: str  # plaintext: returned to the caller once, never stored
    key_id: str
    prefix: str
    role: Role


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_key(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    role: Role | str,
    run_id: str | None = None,
    mailbox_id: str | None = None,
    label: str | None = None,
    expires_at: datetime | None = None,
) -> IssuedKey:
    role = Role(role)
    if role == Role.AGENT and not (run_id and mailbox_id):
        raise InvalidRequest("agent keys must be bound to a run and a mailbox")
    if role != Role.AGENT and (run_id or mailbox_id):
        raise InvalidRequest("only agent keys are bound to a run and a mailbox")
    prefix = secrets.token_hex(6)
    raw = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    row = m.ApiKey(
        id=new_control_id("key"),
        tenant_id=tenant_id,
        project_id=project_id,
        role=str(role),
        run_id=run_id,
        mailbox_id=mailbox_id,
        key_hash=hash_key(raw),
        prefix=prefix,
        label=label,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return IssuedKey(raw, row.id, prefix, role)


def verify_key(session: Session, raw: str, *, now: datetime | None = None) -> Principal:
    """Resolve a presented key to its Principal or raise InvalidCredential."""
    match = _KEY_RE.fullmatch(raw) if len(raw) <= MAX_KEY_CHARS else None
    if match is None:
        raise InvalidCredential("malformed API key")
    digest = hash_key(raw)
    rows = session.execute(select(m.ApiKey).where(m.ApiKey.prefix == match.group(1))).scalars()
    found = None
    for row in rows:  # constant-time comparison of the full-key hash
        if hmac.compare_digest(row.key_hash, digest):
            found = row
    if found is None:
        raise InvalidCredential("unknown API key")
    moment = now or datetime.now(UTC)
    if found.revoked_at is not None and found.revoked_at <= moment:
        raise InvalidCredential("API key revoked")
    if found.expires_at is not None and found.expires_at <= moment:
        raise InvalidCredential("API key expired")
    return Principal(found.tenant_id, found.project_id, Role(found.role), found.run_id,
                     found.mailbox_id, found.id)  # fmt: skip


def revoke_key(session: Session, key_id: str, *, now: datetime | None = None) -> None:
    row = session.get(m.ApiKey, key_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = now or datetime.now(UTC)
        session.flush()


def project_keys(session: Session, tenant_id: str, project_id: str) -> list[IssuedKey]:
    """One controller and one evaluator key for a project (bootstrap / project creation)."""
    return [
        issue_key(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            role=role,
            label=f"{role} (project bootstrap)",
        )  # fmt: skip
        for role in (Role.CONTROLLER, Role.EVALUATOR)
    ]


def bootstrap(session: Session, tenant_name: str, project_name: str) -> dict[str, str]:
    """Create a tenant + project and its first controller/evaluator keys (CLI only)."""
    tenant = repos.create_tenant(session, tenant_name)
    project = repos.create_project(session, tenant.id, project_name)
    keys = project_keys(session, tenant.id, project.id)
    return {
        "tenant_id": tenant.id,
        "project_id": project.id,
        "controller_key": keys[0].api_key,
        "evaluator_key": keys[1].api_key,
    }
