"""Semantic validation beyond the schema: references, fixtures, assertions, limits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from mailsim.domain.clock import parse_ts
from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import parse_mime
from mailsim.scenario.schema import (
    AGENT_OPERATIONS,
    ASSERTION_TYPES,
    TRIGGER_ACTIONS,
    ScenarioBody,
    ScenarioDoc,
)

MAX_FIXTURE_BYTES = 5 * 1024 * 1024


@dataclass
class Issue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class ValidationReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    doc: ScenarioDoc | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise InvalidRequest(
                "scenario validation failed",
                details={"errors": [str(e) for e in self.errors]},
            )


def validate_data(data: object) -> ValidationReport:
    """Schema-level validation of an already-parsed document."""
    report = ValidationReport()
    try:
        report.doc = ScenarioDoc.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            report.errors.append(Issue(loc, err["msg"]))
    return report


def resolve_fixture(base_dir: Path, rel: str) -> Path:
    """Resolve a fixture path, refusing absolute paths and escapes from `base_dir`."""
    if not rel or Path(rel).is_absolute() or "\x00" in rel:
        raise InvalidRequest(f"fixture path must be relative: {rel!r}")
    root = base_dir.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise InvalidRequest(f"fixture path escapes scenario directory: {rel!r}")
    return target


def _dupes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen and v not in out:
            out.append(v)
        seen.add(v)
    return out


def validate_semantics(
    body: ScenarioBody,
    fixture_check: Callable[[str], str | None] | None = None,
) -> ValidationReport:
    """Reference and rule checks. `fixture_check(path)` returns an error text or None."""
    r = ValidationReport()

    def err(path: str, msg: str) -> None:
        r.errors.append(Issue(path, msg))

    persona_ids = [p.id for p in body.personas]
    for d in _dupes(persona_ids):
        err("scenario.personas", f"duplicate persona id {d!r}")
    for d in _dupes([p.email.lower() for p in body.personas]):
        err("scenario.personas", f"duplicate persona email {d!r}")
    for i, p in enumerate(body.personas):
        if "@" not in p.email:
            err(f"scenario.personas[{i}].email", "not an email address")
    pset = set(persona_ids)

    for d in _dupes([p.id for p in body.organization.policies]):
        err("scenario.organization.policies", f"duplicate policy id {d!r}")

    for i, rel in enumerate(body.relationships):
        for side in (rel.source, rel.target):
            if side not in pset:
                err(f"scenario.relationships[{i}]", f"unknown persona {side!r}")
    for i, proj in enumerate(body.projects):
        for who in proj.participants:
            if who not in pset:
                err(f"scenario.projects[{i}]", f"unknown persona {who!r}")
    for d in _dupes([p.id for p in body.projects]):
        err("scenario.projects", f"duplicate project id {d!r}")

    owners = [mb.owner for mb in body.mailboxes]
    for d in _dupes(owners):
        err("scenario.mailboxes", f"more than one mailbox for persona {d!r}")
    for i, mb in enumerate(body.mailboxes):
        if mb.owner not in pset:
            err(f"scenario.mailboxes[{i}].owner", f"unknown persona {mb.owner!r}")
        for j, msg in enumerate(mb.initial_messages):
            problem = fixture_check(msg.fixture) if fixture_check else None
            if problem:
                err(f"scenario.mailboxes[{i}].initial_messages[{j}].fixture", problem)
    oset = set(owners)

    if body.agent.mailbox not in oset:
        err("scenario.agent.mailbox", f"agent mailbox {body.agent.mailbox!r} has no mailbox")
    allowed, prohibited = body.agent.allowed_operations, body.agent.prohibited_operations
    for op in allowed:
        if op not in AGENT_OPERATIONS:
            err("scenario.agent.allowed_operations", f"unknown operation {op!r}")
    for op in prohibited:
        if op not in (*AGENT_OPERATIONS, "external_delivery"):
            err("scenario.agent.prohibited_operations", f"unknown operation {op!r}")
    for op in set(allowed) & set(prohibited):
        err("scenario.agent", f"operation {op!r} is both allowed and prohibited")

    start = None
    try:
        start = parse_ts(body.environment.clock.start_at)
    except SimError as exc:
        err("scenario.environment.clock.start_at", exc.message)

    ev_ids = [e.id for e in body.events]
    for d in _dupes(ev_ids):
        err("scenario.events", f"duplicate event id {d!r}")
    for i, ev in enumerate(body.events):
        base = f"scenario.events[{i}]"
        trig = ev.trigger
        if trig.type == "at":
            if trig.at is None:
                err(f"{base}.trigger.at", "required for trigger type 'at'")
            else:
                try:
                    at = parse_ts(trig.at)
                    if start is not None and at < start:
                        err(f"{base}.trigger.at", "is before the clock start")
                except SimError as exc:
                    err(f"{base}.trigger.at", exc.message)
        elif trig.type == "after_seconds":
            if trig.seconds is None:
                err(f"{base}.trigger.seconds", "required for trigger type 'after_seconds'")
        else:
            if trig.action not in TRIGGER_ACTIONS:
                err(f"{base}.trigger.action", f"unknown action {trig.action!r}")
        recipients = [ev.event.to] if isinstance(ev.event.to, str) else ev.event.to
        if ev.event.from_ not in pset:
            err(f"{base}.event.from", f"unknown persona {ev.event.from_!r}")
        if not recipients:
            err(f"{base}.event.to", "at least one recipient required")
        for rcpt in recipients:
            if rcpt not in pset:
                err(f"{base}.event.to", f"unknown persona {rcpt!r}")
            elif rcpt not in oset:
                err(f"{base}.event.to", f"persona {rcpt!r} has no mailbox")

    for d in _dupes([a.id for a in body.evaluation.assertions]):
        err("scenario.evaluation.assertions", f"duplicate assertion id {d!r}")
    for i, a in enumerate(body.evaluation.assertions):
        if a.type not in ASSERTION_TYPES:
            err(f"scenario.evaluation.assertions[{i}].type", f"unknown assertion type {a.type!r}")
    return r


def validate_scenario(
    data: object,
    base_dir: Path | None = None,
    *,
    limits: MimeLimits | None = None,
) -> ValidationReport:
    """Full validation: schema, references and (when `base_dir` is given) fixture files."""
    report = validate_data(data)
    if report.doc is None:
        return report
    fixture_check: Callable[[str], str | None] | None = None
    if base_dir is not None:
        directory = base_dir

        def fixture_check(rel: str) -> str | None:
            try:
                path = resolve_fixture(directory, rel)
            except InvalidRequest as exc:
                return exc.message
            if not path.is_file():
                return f"fixture not found: {rel}"
            if path.stat().st_size > MAX_FIXTURE_BYTES:
                return f"fixture too large: {rel}"
            try:
                parsed = parse_mime(path.read_bytes(), limits)
            except InvalidRequest as exc:
                return f"fixture {rel} is not a valid message: {exc.message}"
            if parsed.from_ is None:
                return f"fixture {rel} has no From header"
            return None

    sem = validate_semantics(report.doc.scenario, fixture_check)
    report.errors.extend(sem.errors)
    report.warnings.extend(sem.warnings)
    return report
