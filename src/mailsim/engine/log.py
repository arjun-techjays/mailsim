"""Append-only event log with per-run sequence allocation, and the projector registry.

Concurrency rule (spec 4.1 / 5.5): all mutations of a run serialise on a row lock of the
`runs` row (`SELECT ... FOR UPDATE`). The lock holder allocates the next gapless sequence,
inserts the event and updates the projection in the same transaction. A concurrent writer
simply waits; sequences are therefore gapless and unique, and a rolled-back command leaves
neither an event nor a projection change behind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from mailsim.domain.clock import parse_ts
from mailsim.domain.ids import IdGenerator
from mailsim.store import models as m
from mailsim.store.repos import Scope, get_run

PROJECTION_MODELS: tuple[type[m.Base], ...] = (
    m.ProviderId,
    m.SinkMessage,
    m.ScheduledEvent,
    m.Attachment,
    m.Message,
    m.Thread,
    m.Folder,
    m.Mailbox,
    m.WorkProject,
    m.Relationship,
    m.Persona,
    m.Organization,
)

Projector = Callable[[Session, m.Run, dict[str, Any]], None]
PROJECTORS: dict[str, Projector] = {}


def projector(event_type: str) -> Callable[[Projector], Projector]:
    def deco(fn: Projector) -> Projector:
        PROJECTORS[event_type] = fn
        return fn

    return deco


@dataclass
class Event:
    type: str
    payload: dict[str, Any]
    actor: str = "simulator"
    operation: str = ""
    outcome: str = "ok"
    baseline: bool = False
    virtual_time: datetime | None = field(default=None)
    id_counter_after: int | None = None  # set when copying recorded events (replay)


def lock_run(session: Session, scope: Scope, run_id: str) -> m.Run:
    return get_run(session, scope, run_id, lock=True)


def id_generator(run: m.Run) -> IdGenerator:
    return IdGenerator(run.seed, run.id_counter)


def save_ids(run: m.Run, gen: IdGenerator) -> None:
    run.id_counter = gen.counter


def append(session: Session, run: m.Run, event: Event) -> int:
    """Insert the event and apply its projection. Caller must hold the run lock."""
    if event.type == "run.reset":  # the reset event itself belongs to the new epoch
        run.epoch = int(event.payload["epoch"])
    if event.id_counter_after is not None:
        run.id_counter = event.id_counter_after
    seq = run.next_seq
    run.next_seq = seq + 1
    session.add(
        m.EventRow(
            run_id=run.id,
            sequence=seq,
            epoch=run.epoch,
            baseline=event.baseline,
            type=event.type,
            actor=event.actor,
            operation=event.operation or event.type,
            outcome=event.outcome,
            virtual_time=event.virtual_time or run.virtual_time,
            payload=event.payload,
            id_counter_after=run.id_counter,
        )
    )
    session.flush()
    project(session, run, event.type, event.payload)
    return seq


def project(session: Session, run: m.Run, event_type: str, payload: dict[str, Any]) -> None:
    handler = PROJECTORS.get(event_type)
    if handler is not None:
        handler(session, run, payload)
    session.flush()


def wipe_projection(session: Session, run_id: str) -> None:
    for model in PROJECTION_MODELS:
        session.execute(delete(model).where(model.run_id == run_id))  # type: ignore[attr-defined]


# ---- run-level projectors -----------------------------------------------------------


@projector("clock.set")
def _clock_set(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    run.virtual_time = parse_ts(p["at"])
    if p.get("initial"):
        run.start_time = run.virtual_time


@projector("clock.advanced")
def _clock_advanced(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    run.virtual_time = parse_ts(p["to"])


@projector("run.status_changed")
def _status(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    run.status = p["to"]


@projector("run.reset")
def _reset(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    run.epoch = int(p["epoch"])
    wipe_projection(session, run.id)
    run.agent_actions = 0
    run.virtual_time = run.start_time
    run.status = "READY"  # reset restores the baseline; the run must be started again


@projector("agent.action_counted")
def _agent_action(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    run.agent_actions += 1


@projector("run.seeded")
def _seeded(session: Session, run: m.Run, p: dict[str, Any]) -> None:
    return None
