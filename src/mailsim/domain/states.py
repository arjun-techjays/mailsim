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


# PASSED/FAILED are legal transitions but there is deliberately no public engine API that
# takes them in Phase 1: verdicts need the Phase 2 evaluator.
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.DRAFT: frozenset({RunStatus.VALIDATED, RunStatus.ERROR, RunStatus.CANCELLED}),
    RunStatus.VALIDATED: frozenset({RunStatus.READY, RunStatus.ERROR, RunStatus.CANCELLED}),
    RunStatus.READY: frozenset({RunStatus.RUNNING, RunStatus.ERROR, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.PASSED, RunStatus.FAILED, RunStatus.ERROR, RunStatus.CANCELLED}
    ),
    RunStatus.PASSED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.ERROR: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

TERMINAL = frozenset({RunStatus.PASSED, RunStatus.FAILED, RunStatus.ERROR, RunStatus.CANCELLED})


def check_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise Conflict(
            f"illegal run transition {current} -> {target}",
            details={"from": str(current), "to": str(target)},
        )
