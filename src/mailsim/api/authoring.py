"""Scenario / answer-key authoring helpers shared by the control plane and the UX routes.

- `materialize` writes a submitted scenario document and its fixtures into a temp directory
  (the loader and the validator work on files), refusing traversal and oversized input.
- `validate_draft` is the store-nothing validation behind `POST /v1/scenarios/{key}/validate`
  and `POST /v1/scenario-drafts/validate`. Issue paths are dotted (``scenario.personas.1.email``)
  so a form can map them onto its fields.
- `compose_eml` / `summarize_eml` build and summarise .eml fixtures for the scenario builder.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from mailsim.api import schemas as s
from mailsim.api import ux_schemas as u
from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.domain.models import Address
from mailsim.mime.build import build_mime
from mailsim.mime.parse import ParsedAttachment, parse_mime, sanitize_filename
from mailsim.scenario import validator
from mailsim.scenario.answer_key import CHECK_MODELS
from mailsim.scenario.loader import parse_source
from mailsim.scenario.validator import dotted

MAX_FIXTURES = 200
MAX_FIXTURE_TOTAL_BYTES = 25 * 1024 * 1024
MAX_FIXTURE_PATH_CHARS = 255
TEXT_PREVIEW_CHARS = 500
MAX_HTML_PREVIEW_CHARS = 256 * 1024
MAX_FILENAME_CHARS = 150
MAX_ATTACHMENT_TOTAL_BYTES = 10 * 1024 * 1024
_MSGID = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")
_MIME_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")
_TAG = re.compile(r"<[^<>]*>")  # linear: no backtracking over unclosed "<"
_SLUG = re.compile(r"[^a-z0-9]+")


class DraftLike(Protocol):
    source: str | None
    document: dict[str, Any] | None
    fixtures: dict[str, str]


def tmpdir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="mailsim-scn-")


@dataclass
class KeyIssue:
    loc: list[str]  # pydantic location, with the "check" segment restored
    path: str  # the same, dotted
    message: str


def answer_key_errors(exc: ValidationError, data: object) -> list[KeyIssue]:
    """One entry per answer-key schema error. Errors of an assertion's `check:` block are
    reported under ``answer_key.assertions.<i>.check[.<field>]`` (also the check model's own
    cross-field rules, which pydantic places on the assertion), and pydantic's
    "Value error, " prefix is dropped."""
    out = []
    for err in exc.errors()[:200]:
        loc = [str(p) for p in err["loc"]]
        message = str(err["msg"]).removeprefix("Value error, ")
        if len(loc) == 3 and loc[:2] == ["answer_key", "assertions"] and _check_fails(data, loc):
            loc.append("check")
        path = dotted(".".join(loc) or "<root>")
        out.append(KeyIssue(path.split(".") if loc else [], path, message))
    return out


def answer_key_data(source: str | None, document: dict[str, Any] | None) -> object:
    """The answer key a request carries: exactly one of `source` (YAML/JSON text, parsed
    safely) or `document`. InvalidRequest otherwise or when the text does not parse."""
    if (source is None) == (document is None):
        raise InvalidRequest("provide exactly one of `source` or `document`")
    if source is not None:
        return parse_source(source.encode(), is_json=source.lstrip().startswith("{"))
    return document


def _check_fails(data: object, loc: list[str]) -> bool:
    """Whether the assertion at `loc` has a `check:` block its type's check model rejects."""
    try:
        assertion = data["answer_key"]["assertions"][int(loc[2])]  # type: ignore[index]
        CHECK_MODELS[assertion["type"]].model_validate(assertion["check"])
    except ValidationError:
        return True
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return False


def issue(path: str, message: str) -> s.ValidationIssue:
    return s.ValidationIssue(path=dotted(path), message=message)


def check_fixture_path(rel: str) -> None:
    """Fixture paths are relative, without empty / '.' / '..' parts, and never the document."""
    if len(rel) > MAX_FIXTURE_PATH_CHARS:
        raise InvalidRequest(
            f"fixture path too long: {rel[:60]!r}...", details={"path": "fixtures"}
        )
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise InvalidRequest(f"fixture path must not contain empty, '.' or '..' parts: {rel!r}",
                             details={"path": "fixtures"})  # fmt: skip
    if rel.split("/")[0] in ("scenario.json", "scenario.yaml"):
        raise InvalidRequest(f"fixture path collides with the scenario document: {rel!r}",
                             details={"path": "fixtures"})  # fmt: skip
    validator.resolve_fixture(Path("/fixtures-root"), rel)  # absolute paths, NUL, escapes


def _decode_fixtures(fixtures: dict[str, str]) -> dict[str, bytes]:
    if len(fixtures) > MAX_FIXTURES:
        raise InvalidRequest(f"at most {MAX_FIXTURES} fixtures per request",
                             details={"path": "fixtures"})  # fmt: skip
    decoded: dict[str, bytes] = {}
    total = 0
    for rel, encoded in fixtures.items():
        check_fixture_path(rel)
        if len(encoded) > s.MAX_FIXTURE_B64_CHARS:  # checked before decoding
            raise InvalidRequest(f"fixture too large: {rel}", details={"path": "fixtures"})
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise InvalidRequest(f"fixture {rel!r} is not valid base64",
                                 details={"path": "fixtures"}) from None  # fmt: skip
        if len(content) > validator.MAX_FIXTURE_BYTES:
            raise InvalidRequest(f"fixture too large: {rel}", details={"path": "fixtures"})
        total += len(content)
        if total > MAX_FIXTURE_TOTAL_BYTES:
            raise InvalidRequest(f"fixtures exceed {MAX_FIXTURE_TOTAL_BYTES} bytes in total",
                                 details={"path": "fixtures"})  # fmt: skip
        decoded[rel] = content
    return decoded


def materialize(body: DraftLike, directory: Path) -> Path:
    """Write the submitted document (+ fixtures) into a fresh temp dir; never outside it."""
    if (body.source is None) == (body.document is None):
        raise InvalidRequest("provide exactly one of `source` or `document`")
    if body.document is not None:
        target, data = directory / "scenario.json", json.dumps(body.document).encode()
    else:
        assert body.source is not None
        is_json = body.source.lstrip().startswith("{")
        target = directory / ("scenario.json" if is_json else "scenario.yaml")
        data = body.source.encode()
    decoded = _decode_fixtures(body.fixtures)
    target.write_bytes(data)
    for rel, content in decoded.items():
        path = validator.resolve_fixture(directory, rel)  # rejects absolute paths, traversal
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError:  # e.g. both "a" and "a/b": one path is a file, the other needs a dir
            raise InvalidRequest(f"fixture paths overlap: {rel!r}",
                                 details={"path": "fixtures"}) from None  # fmt: skip
    return target


@dataclass
class DraftReport:
    errors: list[s.ValidationIssue]
    warnings: list[s.ValidationIssue]
    document: dict[str, Any] | None
    scenario_id: str | None


def validate_draft(body: DraftLike) -> DraftReport:
    """Schema, references, fixtures and assertions; stores nothing.

    Request problems (bad base64, fixture paths, both/neither of source and document) raise
    InvalidRequest (HTTP 400); everything about the document itself is reported as issues."""
    with tmpdir() as tmp:
        path = materialize(body, Path(tmp).resolve())
        try:
            data = parse_source(path.read_bytes(), is_json=path.suffix == ".json")
        except InvalidRequest as exc:
            return DraftReport([issue("<source>", exc.message)], [], None, None)
        report = validator.validate_scenario(data, path.parent)
    document = data if isinstance(data, dict) else None
    return DraftReport(
        errors=[issue(i.path, i.message) for i in report.errors],
        warnings=[issue(i.path, i.message) for i in report.warnings],
        document=document,
        scenario_id=report.doc.scenario.id if report.doc is not None else None,
    )


# ---- .eml fixtures -----------------------------------------------------------------------
def _no_newlines(field: str, value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise InvalidRequest(f"header injection: {field} must not contain CR, LF or NUL",
                             details={"field": field})  # fmt: skip
    return value


def _address(field: str, value: str) -> Address:
    _no_newlines(field, value)
    name, email = parseaddr(value)
    email = email.strip().lower()
    if not email or "@" not in email or any(ch.isspace() for ch in email):
        raise InvalidRequest(f"{field}: not an email address: {value[:100]!r}",
                             details={"field": field})  # fmt: skip
    return Address(email=email, name=name.strip() or None)


def _message_id(field: str, value: str) -> str:
    value = _no_newlines(field, value).strip()
    if not value.startswith("<"):
        value = f"<{value}>"
    if not _MSGID.fullmatch(value) or len(value) > 998:
        raise InvalidRequest(f"{field}: not a Message-ID (<local@domain>): {value[:100]!r}",
                             details={"field": field})  # fmt: skip
    return value


def slugify(text: str, max_chars: int = 40) -> str:
    slug = _SLUG.sub("-", text.lower()).strip("-")[:max_chars].strip("-")
    return slug or "message"


def compose_eml(body: u.ComposeMessage, *, now: datetime | None = None) -> u.ComposedFixture:
    """Build an RFC 5322 fixture. `Date` defaults to the current UTC time (not deterministic:
    the bytes are what gets stored), `Message-ID` to ``<uuid@domain of From>``."""
    sender = _address("from", body.from_)
    to = [_address("to", a) for a in body.to]
    cc = [_address("cc", a) for a in body.cc]
    subject = _no_newlines("subject", body.subject)
    if body.date is not None:
        _no_newlines("date", body.date)
        try:
            date = parse_ts(body.date)
        except SimError:
            raise InvalidRequest(f"date: not an ISO 8601 timestamp: {body.date[:60]!r}",
                                 details={"field": "date"}) from None  # fmt: skip
    else:
        date = (now or datetime.now(UTC)).replace(microsecond=0)
    domain = sender.email.rsplit("@", 1)[1]
    message_id = (_message_id("message_id", body.message_id) if body.message_id
                  else f"<{uuid.uuid4().hex}@{domain}>")  # fmt: skip
    in_reply_to = _message_id("in_reply_to", body.in_reply_to) if body.in_reply_to else None
    references = [_message_id("references", r) for r in body.references]
    if in_reply_to is not None and in_reply_to not in references:
        references.append(in_reply_to)
    attachments = []
    total = 0
    for att in body.attachments:
        # filenames are sanitised (no path, control characters, CR/LF; quotes are escaped and
        # non-ASCII is RFC 2231-encoded by the MIME writer); unknown types become octet-stream
        filename = sanitize_filename(att.filename.replace('"', "'"))[:MAX_FILENAME_CHARS]
        ctype = att.content_type.strip().lower()
        if not _MIME_TYPE.fullmatch(ctype):
            ctype = "application/octet-stream"
        try:
            content = base64.b64decode(att.content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise InvalidRequest(f"attachment {filename!r} is not valid base64",
                                 details={"field": "attachments.content_b64"}) from None  # fmt: skip
        total += len(content)
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise InvalidRequest(f"attachments exceed {MAX_ATTACHMENT_TOTAL_BYTES} bytes in total",
                                 details={"field": "attachments"})  # fmt: skip
        attachments.append(ParsedAttachment(filename, ctype, content, False, None))
    path = body.fixture_path or (
        f"fixtures/{slugify(subject)}-{message_id.strip('<>').split('@')[0][:8]}.eml"
    )
    check_fixture_path(_no_newlines("fixture_path", path))
    try:
        raw = build_mime(message_id=message_id, from_=sender, to=to, cc=cc, subject=subject,
                         date=date, text=body.text if body.text is not None else "",
                         html=body.html, in_reply_to=in_reply_to, references=references,
                         attachments=attachments)  # fmt: skip
    except (ValueError, TypeError) as exc:  # stdlib header validation
        raise InvalidRequest(f"cannot build the message: {exc}"[:300]) from None
    if len(raw) > validator.MAX_FIXTURE_BYTES:
        raise InvalidRequest(f"message is larger than {validator.MAX_FIXTURE_BYTES} bytes",
                             details={"size": len(raw)})  # fmt: skip
    parse_mime(raw)  # what the scenario validator will do with it (attachment type limits)
    return u.ComposedFixture(path=path, content_b64=base64.b64encode(raw).decode(),
                             message_id=message_id, size=len(raw))  # fmt: skip


def _fmt(addr: Address) -> str:
    return f"{addr.name} <{addr.email}>" if addr.name else addr.email


def summarize_eml(content_b64: str) -> u.EmlSummary:
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidRequest("content_b64 is not valid base64") from None
    if len(raw) > validator.MAX_FIXTURE_BYTES:
        raise InvalidRequest(f"message is larger than {validator.MAX_FIXTURE_BYTES} bytes")
    parsed = parse_mime(raw)
    if parsed.from_ is None:
        raise InvalidRequest("not an RFC 5322 message: there is no From header")
    text = parsed.body_text
    if not text and parsed.body_html:
        text = " ".join(_TAG.sub(" ", parsed.body_html[:MAX_HTML_PREVIEW_CHARS]).split())
    return u.EmlSummary.model_validate({
        "message_id": parsed.message_id,
        "from": _fmt(parsed.from_) if parsed.from_ else None,
        "to": [_fmt(a) for a in parsed.to], "cc": [_fmt(a) for a in parsed.cc],
        "subject": parsed.subject, "date": iso(parsed.date) if parsed.date else None,
        "in_reply_to": parsed.in_reply_to, "references": parsed.references,
        "attachments": [a.filename for a in parsed.attachments],
        "text_preview": (text or "")[:TEXT_PREVIEW_CHARS],
    })  # fmt: skip
