"""Run lifecycle state machine (spec 6.2)."""

from __future__ import annotations

from enum import StrEnum

from mailsim.domain.errors import Conflict


class RunStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    READY = "READY"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    REPLAYED = "REPLAYED"  # a replay copy of another run's log: final, never an agent run


# Accuracy reports group runs created WITHOUT an agent build (stored as null) under this
# name; run creation refuses it as a build name so it can never merge with a real build.
NO_BUILD = "(no build)"


# PASSED/FAILED are legal transitions but there is deliberately no public engine API that
# takes them in Phase 1: verdicts need the Phase 2 evaluator.
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    # every live status can end in REPLAYED: only a replay copy ever takes that transition
    RunStatus.DRAFT: frozenset(
        {RunStatus.VALIDATED, RunStatus.ERROR, RunStatus.CANCELLED, RunStatus.REPLAYED}
    ),
    RunStatus.VALIDATED: frozenset(
        {RunStatus.READY, RunStatus.ERROR, RunStatus.CANCELLED, RunStatus.REPLAYED}
    ),
    RunStatus.READY: frozenset(
        {RunStatus.RUNNING, RunStatus.ERROR, RunStatus.CANCELLED, RunStatus.REPLAYED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PASSED,
            RunStatus.FAILED,
            RunStatus.ERROR,
            RunStatus.CANCELLED,
            RunStatus.REPLAYED,
        }
    ),
    RunStatus.PASSED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.ERROR: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.REPLAYED: frozenset(),
}

TERMINAL = frozenset(
    {RunStatus.PASSED, RunStatus.FAILED, RunStatus.ERROR, RunStatus.CANCELLED, RunStatus.REPLAYED}
)


def check_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise Conflict(
            f"illegal run transition {current} -> {target}",
            details={"from": str(current), "to": str(target)},
        )
