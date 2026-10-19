"""Reference checks for answer-key assertions: every name a check uses must exist.

Schema validation only proves a check is well formed. A typo in a folder, operation, outcome,
persona mailbox or event id would still parse and then silently PASS (`count: {eq: 0}` of
something that can never happen) or stay INCONCLUSIVE forever. These checks run at validate
AND publish time (file answer keys and inline scenario assertions) against fixed enumerations
and against every scenario version the key is compatible with.

Issue paths are dotted, e.g. ``answer_key.assertions.3.check.where.folder``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mailsim.domain.errors import ErrorCode
from mailsim.domain.models import SYSTEM_FOLDERS, VALID_FLAGS
from mailsim.scenario.answer_key import (
    ActionMatch,
    AnswerAssertion,
    ContentCheck,
    EventRef,
    MessageWhere,
    PolicyCheck,
    ProtocolCheck,
    StateCheck,
    TemporalCheck,
)
from mailsim.scenario.schema import GRADED_AGENT_OPERATIONS, ScenarioBody

FOLDERS: tuple[str, ...] = tuple(kind for _name, kind in SYSTEM_FOLDERS)
# Operation names as the engine records them in the action log, per actor.
OPERATIONS: dict[str, tuple[str, ...]] = {
    "agent": GRADED_AGENT_OPERATIONS,
    "simulator": ("persona.reply", "persona.failed"),
    "controller": ("create_run", "start", "cancel", "error", "reset", "inject", "tick",
                   "advance", "replay"),
}  # fmt: skip
ERROR_CODES: tuple[str, ...] = tuple(code.value for code in ErrorCode)
OUTCOMES: tuple[str, ...] = ("ok", "any", *ERROR_CODES)


@dataclass(frozen=True)
class ScenarioRefs:
    """The names one scenario version defines."""

    version: int
    mailboxes: frozenset[str]  # persona ids that own a mailbox
    personas: frozenset[str]
    events: frozenset[str]

    @classmethod
    def of(cls, version: int, body: ScenarioBody) -> ScenarioRefs:
        return cls(version, frozenset(mb.owner for mb in body.mailboxes),
                   frozenset(p.id for p in body.personas),
                   frozenset(e.id for e in body.events))  # fmt: skip


@dataclass(frozen=True)
class RefIssue:
    path: str
    message: str


def _one_of(values: Iterable[str]) -> str:
    return ", ".join(values)


def _versions(versions: list[int]) -> str:
    return f"scenario version{'s' if len(versions) > 1 else ''} {_one_of(map(str, versions))}"


class _Checker:
    def __init__(self, scenarios: list[ScenarioRefs]) -> None:
        self.scenarios = scenarios
        self.issues: list[RefIssue] = []

    def err(self, path: str, message: str) -> None:
        self.issues.append(RefIssue(path, message))

    # ---- version-dependent names ------------------------------------------------------
    def mailbox(self, path: str, persona: str) -> None:
        missing = [s for s in self.scenarios if persona not in s.mailboxes]
        if not missing:
            return
        versions = [s.version for s in missing]
        if all(persona in s.personas for s in missing):
            self.err(path, f"persona {persona!r} has no mailbox in {_versions(versions)}")
            return
        known = sorted(set().union(*(s.mailboxes for s in missing)))
        self.err(path, f"unknown persona mailbox {persona!r} in {_versions(versions)}"
                       f" (mailboxes: {_one_of(known) or 'none'})")  # fmt: skip

    def event(self, path: str, event_id: str) -> None:
        missing = [s for s in self.scenarios if event_id not in s.events]
        if missing:
            known = sorted(set().union(*(s.events for s in missing)))
            self.err(path, f"unknown event {event_id!r} in {_versions([s.version for s in missing])}"
                           f" (events: {_one_of(known) or 'none'})")  # fmt: skip

    # ---- fixed enumerations -----------------------------------------------------------
    def where(self, path: str, where: MessageWhere) -> None:
        if where.mailbox is not None:
            self.mailbox(f"{path}.mailbox", where.mailbox)
        if where.folder is not None and where.folder.casefold() not in FOLDERS:
            self.err(f"{path}.folder",
                     f"unknown folder {where.folder!r}: use one of {_one_of(FOLDERS)}")  # fmt: skip
        for field in ("has_flag", "lacks_flag"):
            flag = getattr(where, field)
            if flag is not None and flag.upper() not in VALID_FLAGS:
                self.err(f"{path}.{field}",
                         f"unknown flag {flag!r}: use one of {_one_of(sorted(VALID_FLAGS))}")  # fmt: skip

    def action(self, path: str, match: ActionMatch) -> None:
        known = OPERATIONS[match.actor]
        if match.operation not in known:
            self.err(f"{path}.operation", f"unknown {match.actor} operation {match.operation!r}: "
                                          f"use one of {_one_of(known)}")  # fmt: skip
        if match.outcome not in OUTCOMES:
            self.err(f"{path}.outcome",
                     f"unknown outcome {match.outcome!r}: use one of {_one_of(OUTCOMES)}")  # fmt: skip

    def event_ref(self, path: str, ref: EventRef) -> None:
        if ref.event is not None:
            self.event(f"{path}.event", ref.event)
        if ref.action is not None:
            self.action(f"{path}.action", ref.action)

    def assertion(self, path: str, a: AnswerAssertion) -> None:
        if a.evaluate_at.event is not None:
            self.event(f"{path}.evaluate_at.event", a.evaluate_at.event)
        check = a.parsed_check()
        cp = f"{path}.check"
        if isinstance(check, StateCheck | ContentCheck):
            self.where(f"{cp}.where", check.where)
        elif isinstance(check, ActionMatch):
            self.action(cp, check)
        elif isinstance(check, PolicyCheck):
            self.action(f"{cp}.forbid", check.forbid)
            if check.unless is not None:
                self.where(f"{cp}.unless.where", check.unless.where)
        elif isinstance(check, TemporalCheck):
            self.event_ref(f"{cp}.first", check.first)
            self.event_ref(f"{cp}.then", check.then)
        elif isinstance(check, ProtocolCheck):
            for i, code in enumerate(check.error_codes):
                if code not in ERROR_CODES:
                    self.err(f"{cp}.error_codes.{i}",
                             f"unknown error code {code!r}: use one of {_one_of(ERROR_CODES)}")  # fmt: skip


def reference_issues(
    assertions: list[AnswerAssertion],
    scenarios: list[ScenarioRefs],
    prefix: str = "answer_key.assertions",
) -> list[RefIssue]:
    """Unknown references of `assertions` (ERRORS). Version-dependent names (persona
    mailboxes, event ids) must exist in every scenario version in `scenarios`."""
    checker = _Checker(scenarios)
    for i, a in enumerate(assertions):
        checker.assertion(f"{prefix}.{i}", a)
    return checker.issues


__all__ = ["ERROR_CODES", "FOLDERS", "OPERATIONS", "OUTCOMES", "RefIssue", "ScenarioRefs",
           "reference_issues"]  # fmt: skip
