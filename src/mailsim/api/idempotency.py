"""Idempotency-Key handling for operations with side effects (spec 7.1).

Order: the route's role dependency authorizes FIRST; only then is the key looked up, scoped
to (tenant, project, key, credential id) so one principal can never replay another's
response. Same key + same request => the stored status and body are replayed (header
``Idempotency-Replayed: true``); same key + different request => 409 CONFLICT. Successful
responses are stored for 24 hours; failed requests are not stored and may be retried.

Concurrent duplicates are serialised with a transaction-scoped advisory lock on the key, so
the operation runs at most once. Response fields named in ``redact`` (secrets such as
freshly issued API keys) are never written to the table: a replay returns them redacted.
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
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from mailsim.auth.principal import Principal
from mailsim.domain.errors import Conflict, InvalidRequest
from mailsim.store import models as m

TTL = timedelta(hours=24)
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
    method_path = f"{request.method} {request.url.path}"
    digest = request_hash(request.method, request.url.path, request.url.query, payload)
    ident = (principal.tenant_id, principal.project_id, key, principal.key_id)
    with sf.begin() as s:
        s.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("|".join(ident), 0))))
        row = s.get(m.IdempotencyKey, ident)
        moment = now()
        if row is not None and row.expires_at <= moment:
            s.execute(
                delete(m.IdempotencyKey).where(
                    m.IdempotencyKey.tenant_id == ident[0],
                    m.IdempotencyKey.project_id == ident[1],
                    m.IdempotencyKey.key == ident[2],
                    m.IdempotencyKey.key_id == ident[3],
                )
            )
            row = None
        if row is not None:
            if row.request_hash != digest:  # the hash covers method, path, query and body
                raise Conflict(
                    "Idempotency-Key was already used for a different request",
                    details={"idempotency_key": key},
                )
            return JSONResponse(row.response, status_code=row.status_code,
                                headers={REPLAYED_HEADER: "true"})  # fmt: skip
        body = _dump(operation())
        s.execute(
            pg_insert(m.IdempotencyKey)
            .values(
                tenant_id=ident[0],
                project_id=ident[1],
                key=ident[2],
                key_id=ident[3],
                method_path=method_path,
                request_hash=digest,
                status_code=status_code,
                response=_redact(body, redact),
                expires_at=moment + TTL,
            )  # fmt: skip
            .on_conflict_do_nothing()
        )
    return JSONResponse(body, status_code=status_code)
