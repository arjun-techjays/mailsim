"""Semantic validation beyond the schema: references, fixtures, assertions, limits."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from mailsim.domain.clock import parse_ts
from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import parse_mime
from mailsim.scenario.answer_key import (
    AnswerAssertion,
    AnswerKeyBody,
    find_leaks,
    secret_strings,
)
from mailsim.scenario.schema import (
    AGENT_OPERATIONS,
    TRIGGER_ACTIONS,
    ScenarioBody,
    ScenarioDoc,
)

MAX_FIXTURE_BYTES = 5 * 1024 * 1024
_INDEX = re.compile(r"\[(\d+)\]")
_ASSERTION = re.compile(r"^(.*\bassertions\.\d+)\.([^.]+)")
_ASSERTION_FIELDS = frozenset(AnswerAssertion.model_fields)
MAX_ADDRESS_CHARS = 254
_SCENARIO_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_ID_RULE = ("use letters, digits, '.', '_' or '-' (starting with a letter or digit), at most "
            "64 characters")  # fmt: skip
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DOMAIN = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_ADDRESS = re.compile(rf"[A-Za-z0-9.!#$%&'*+/=?^_`{{|}}~-]+@{_LABEL}(?:\.{_LABEL})+")


def is_domain(value: str) -> bool:
    """A DNS name with at least two labels (persona / organization domains)."""
    return len(value) <= MAX_ADDRESS_CHARS and _DOMAIN.fullmatch(value) is not None


def is_address(value: str) -> bool:
    """One bare addr-spec: ASCII local part and domain labels; no display name, whitespace,
    control characters, angle brackets or commas (header-injection safe)."""
    return len(value) <= MAX_ADDRESS_CHARS and _ADDRESS.fullmatch(value) is not None


def dotted(path: str) -> str:
    """Form-mappable paths: ``scenario.personas[1].email`` -> ``scenario.personas.1.email``;
    errors inside an assertion's `check:` block (validated by the assertion as a whole) get
    their ``check`` segment back: ``...assertions.0.count.min`` -> ``...0.check.count.min``."""
    path = _INDEX.sub(r".\1", path)
    match = _ASSERTION.match(path)
    if match and match.group(2) not in _ASSERTION_FIELDS:
        path = f"{match.group(1)}.check.{match.group(2)}{path[match.end() :]}"
    return path


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
                details={
                    "errors": [str(e) for e in self.errors],
                    "issues": [{"path": dotted(e.path), "message": e.message} for e in self.errors],
                },
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

    if len(body.id) > 100 or not _SCENARIO_ID.fullmatch(body.id):
        err("scenario.id", "use lowercase letters, digits, '.', '_' or '-' (starting with a "
            "letter or digit), at most 100 characters")  # fmt: skip
    if not body.title.strip():
        err("scenario.title", "must not be empty")
    for i, p in enumerate(body.personas):
        if len(p.id) > 64 or not _ID.fullmatch(p.id):
            err(f"scenario.personas[{i}].id", _ID_RULE)
        if not p.role.strip():
            err(f"scenario.personas[{i}].role", "must not be empty")
    for i, ev in enumerate(body.events):
        if len(ev.id) > 64 or not _ID.fullmatch(ev.id):
            err(f"scenario.events[{i}].id", _ID_RULE)
    persona_ids = [p.id for p in body.personas]
    for d in _dupes(persona_ids):
        err("scenario.personas", f"duplicate persona id {d!r}")
    for d in _dupes([p.email.lower() for p in body.personas]):
        err("scenario.personas", f"duplicate persona email {d!r}")
    for i, p in enumerate(body.personas):
        if not is_address(p.email):
            err(f"scenario.personas[{i}].email",
                "not an email address: use a plain local@domain.tld (ASCII, no spaces, names, "
                "brackets or commas, at most 254 characters)")  # fmt: skip
    if not is_domain(body.organization.domain):
        err("scenario.organization.domain", "not a domain name (labels of letters, digits, '-')")
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
    for i, p in enumerate(body.personas):
        for j, rule in enumerate(p.behavior.script):
            if rule.on.from_ is not None and rule.on.from_ not in pset:
                err(f"scenario.personas[{i}].behavior.script[{j}].on.from",
                    f"unknown persona {rule.on.from_!r}")  # fmt: skip
        if p.behavior.script and p.behavior.mode != "scripted":
            err(f"scenario.personas[{i}].behavior", "script rules require mode: scripted")
    leaks = find_leaks(_inline_secrets(body), agent_visible_texts(body))
    for leak in leaks:
        err("scenario", f"evaluation text leaks into agent-visible content: {leak[:60]!r}")
    return r


def _inline_secrets(body: ScenarioBody) -> list[str]:
    # model_construct: duplicates are reported separately, never crash the leak scan
    key = AnswerKeyBody.model_construct(
        scenario=body.id, version=1, assertions=body.evaluation.assertions
    )
    return secret_strings(key)


def agent_visible_texts(body: ScenarioBody, fixtures: list[bytes] | None = None) -> list[str]:
    """Everything an agent could ever read that comes from the scenario author."""
    texts: list[str] = []  # title/tags/evaluation are never shown to the agent
    for ev in body.events:
        texts += [ev.event.subject, ev.event.body]
    for p in body.personas:
        texts += [r.reply.body for r in p.behavior.script]
        texts += [r.reply.subject for r in p.behavior.script if r.reply.subject]
    for raw in fixtures or []:
        texts.append(raw.decode("utf-8", "replace"))
    return texts


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
    if base_dir is not None:
        raws = []
        for mb in report.doc.scenario.mailboxes:
            for im in mb.initial_messages:
                try:
                    raws.append(resolve_fixture(base_dir, im.fixture).read_bytes())
                except (InvalidRequest, OSError):
                    continue  # already reported by fixture_check
        for leak in find_leaks(
            _inline_secrets(report.doc.scenario), agent_visible_texts(report.doc.scenario, raws)
        ):
            if not any(leak[:60] in e.message for e in sem.errors):
                sem.errors.append(
                    Issue(
                        "scenario.fixtures", f"evaluation text leaks into a fixture: {leak[:60]!r}"
                    )
                )
    report.errors.extend(sem.errors)
    report.warnings.extend(sem.warnings)
    return report
