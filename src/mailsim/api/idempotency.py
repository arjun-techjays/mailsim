"""Idempotency-Key handling for operations with side effects (spec 7.1).

Order: the route's role dependency authorizes FIRST; only then is the key looked up, scoped
to (tenant, project, key, credential id) so one principal can never replay another's
response.

Two-phase record, so no connection is held while the operation runs:
1. a short transaction claims the key by inserting a *pending* row (``status_code = 0``);
   an existing pending row answers 409 CONFLICT ("in progress"), a finished row is replayed
   (same request hash, header ``Idempotency-Replayed: true``) or answers 409 (different
   request); an expired row is replaced;
2. the operation runs with no session open;
3. a second short transaction stores the response (24 hours), or deletes the claim when the
   operation failed, so failed requests may be retried.

Pending claims expire after ``PENDING_TTL`` so a crashed worker cannot block a key for long.
Response fields named in ``redact`` (secrets such as freshly issued API keys) are never
written to the table: a replay returns them redacted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from mailsim.auth.principal import Principal
from mailsim.domain.errors import Conflict, InvalidRequest
from mailsim.store import models as m

TTL = timedelta(hours=24)
PENDING_TTL = timedelta(minutes=10)
PENDING = 0  # status_code of a claimed key whose operation has not finished
REPLAYED_HEADER = "Idempotency-Replayed"
REDACTED = "<redacted: shown once>"


def request_hash(method: str, path: str, query: str, payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method}\n{path}\n{query}\n{body}".encode()).hexdigest()


def _redact(value: Any, fields: Sequence[str]) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if k in fields else _redact(v, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, fields) for v in value]
    return value


def _dump(result: BaseModel) -> dict[str, Any]:
    return result.model_dump(mode="json", by_alias=True)


def _ident_filter(ident: tuple[str, str, str, str]) -> Any:
    k = m.IdempotencyKey
    return (
        (k.tenant_id == ident[0])
        & (k.project_id == ident[1])
        & (k.key == ident[2])
        & (k.key_id == ident[3])
    )


def idempotent(
    request: Request,
    principal: Principal,
    key: str | None,
    payload: Any,
    status_code: int,
    operation: Callable[[], BaseModel],
    *,
    redact: Sequence[str] = (),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JSONResponse:
    """Run `operation` at most once per (principal, Idempotency-Key)."""
    if key is None:
        return JSONResponse(_dump(operation()), status_code=status_code)
    if not key.strip():
        raise InvalidRequest("Idempotency-Key must not be empty")
    if principal.key_id is None:  # pragma: no cover - every API principal comes from a key
        raise InvalidRequest("idempotency requires a credential-bound principal")
    sf: sessionmaker[Session] = request.app.state.session_factory
    digest = request_hash(request.method, request.url.path, request.url.query, payload)
    ident = (principal.tenant_id, principal.project_id, key, principal.key_id)
    where = _ident_filter(ident)

    # phase 1: claim the key (short transaction)
    with sf.begin() as s:
        moment = now()
        s.execute(delete(m.IdempotencyKey).where(where, m.IdempotencyKey.expires_at <= moment))
        sweep_expired(s, moment, tenant_id=ident[0], project_id=ident[1])
        claimed = s.execute(
            pg_insert(m.IdempotencyKey)
            .values(
                tenant_id=ident[0],
                project_id=ident[1],
                key=ident[2],
                key_id=ident[3],
                method_path=f"{request.method} {request.url.path}",
                request_hash=digest,
                status_code=PENDING,
                response={},
                expires_at=moment + PENDING_TTL,
            )  # fmt: skip
            .on_conflict_do_nothing()
            .returning(m.IdempotencyKey.key)
        ).scalar_one_or_none()
        if claimed is None:
            row = s.execute(select(m.IdempotencyKey).where(where)).scalar_one()
            if row.request_hash != digest:  # the hash covers method, path, query and body
                raise Conflict("Idempotency-Key was already used for a different request",
                               details={"idempotency_key": key})  # fmt: skip
            if row.status_code == PENDING:
                raise Conflict("a request with this Idempotency-Key is in progress",
                               details={"idempotency_key": key})  # fmt: skip
            return JSONResponse(row.response, status_code=row.status_code,
                                headers={REPLAYED_HEADER: "true"})  # fmt: skip

    # phase 2: the operation itself, with no connection held by this layer
    try:
        body = _dump(operation())
    except BaseException:
        with sf.begin() as s:
            s.execute(
                delete(m.IdempotencyKey).where(where, m.IdempotencyKey.status_code == PENDING)
            )
        raise

    # phase 3: record the response for replays
    with sf.begin() as s:
        s.execute(
            update(m.IdempotencyKey)
            .where(where, m.IdempotencyKey.status_code == PENDING)
            .values(
                status_code=status_code, response=_redact(body, redact), expires_at=now() + TTL
            )  # fmt: skip
        )
    return JSONResponse(body, status_code=status_code)


SWEEP_BATCH = 500


def sweep_expired(session: Session, moment: datetime, *, tenant_id: str | None = None,
                  project_id: str | None = None, limit: int = SWEEP_BATCH) -> int:  # fmt: skip
    """Delete up to `limit` expired idempotency records (optionally within one project).
    Called opportunistically on every claim, and by `python -m mailsim.serve sweep`."""
    ik = m.IdempotencyKey
    victims = select(ik.tenant_id, ik.project_id, ik.key, ik.key_id).where(ik.expires_at <= moment)
    if tenant_id is not None:
        victims = victims.where(ik.tenant_id == tenant_id, ik.project_id == project_id)
    rows = session.execute(victims.limit(limit)).all()
    for row in rows:
        session.execute(delete(ik).where(ik.tenant_id == row[0], ik.project_id == row[1],
                                         ik.key == row[2], ik.key_id == row[3]))  # fmt: skip
    return len(rows)
