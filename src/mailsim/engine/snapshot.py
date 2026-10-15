"""Canonical state snapshot + hash, used to prove replay reproduces identical state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from mailsim.domain.clock import iso
from mailsim.store import models as m

_TABLES: tuple[type[Any], ...] = (
    m.Organization,
    m.Persona,
    m.Relationship,
    m.WorkProject,
    m.Mailbox,
    m.Folder,
    m.Thread,
    m.Message,
    m.Attachment,
    m.ScheduledEvent,
    m.SinkMessage,
    m.ProviderId,
)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return iso(value)
    return value


def snapshot(session: Session, run_id: str) -> dict[str, Any]:
    run = session.get(m.Run, run_id)
    assert run is not None
    out: dict[str, Any] = {
        "run": {
            "status": run.status,
            "epoch": run.epoch,
            "virtual_time": iso(run.virtual_time) if run.virtual_time else None,
            "agent_actions": run.agent_actions,
            "id_counter": run.id_counter,
        }
    }
    for model in _TABLES:
        attrs = [a for a in inspect(model).column_attrs if a.key != "run_id"]
        pk = [a for a in attrs if a.columns[0].primary_key]
        rows = session.execute(
            select(model)
            .where(model.run_id == run_id)
            .order_by(*[getattr(model, a.key) for a in pk])
        ).scalars()
        out[model.__tablename__] = [
            {a.columns[0].name: _jsonable(getattr(row, a.key)) for a in attrs} for row in rows
        ]
    return out


def state_hash(snap: dict[str, Any]) -> str:
    payload = json.dumps(snap, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
