"""Parse raw RFC 5322 / MIME bytes into canonical fields.

Uses the stdlib `email` package with `policy.default`. Content is only ever decoded as
data: nothing is executed, rendered or decompressed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import PurePosixPath, PureWindowsPath

from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import Address
from mailsim.mime.limits import MimeLimits

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_MSGID = re.compile(r"<[^<>\s]+>")


@dataclass(frozen=True)
class ParsedAttachment:
    filename: str
    mime_type: str
    content: bytes
    inline: bool
    content_id: str | None

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class ParsedMessage:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_: Address | None
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    subject: str
    body_text: str | None
    body_html: str | None
    date: datetime | None
    headers: list[tuple[str, str]]
    attachments: list[ParsedAttachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sanitize_filename(name: str | None, fallback: str = "attachment") -> str:
    """Strip any path components and control characters (path-traversal safe)."""
    if not name:
        return fallback
    base = PureWindowsPath(PurePosixPath(name.replace("\\", "/")).name).name
    base = _CTRL.sub("", base).strip(" .\t")
    return base[:255] or fallback


def _addresses(msg: Message, header: str) -> list[Address]:
    values = [str(v) for v in msg.get_all(header, [])]
    return [Address(email=a.lower(), name=n or None) for n, a in getaddresses(values) if a]


def _msgids(value: str | None) -> list[str]:
    return _MSGID.findall(value or "")


def _decode_text(part: Message) -> str:
    assert isinstance(part, EmailMessage)
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):  # unknown charset: degrade, never fail
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    return str(content).replace("\r\n", "\n")


def _walk(part: Message, out: ParsedMessage, limits: MimeLimits, depth: int = 0) -> None:
    if depth > limits.max_depth:
        raise InvalidRequest("MIME structure too deeply nested", details={"max": limits.max_depth})
    ctype = part.get_content_type()
    if part.is_multipart() and ctype != "message/rfc822":
        for sub in part.iter_parts():  # type: ignore[attr-defined]
            _walk(sub, out, limits, depth + 1)
        return

    disposition = (part.get_content_disposition() or "").lower()
    filename = part.get_filename()
    if ctype == "message/rfc822":
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        content = inner.as_bytes(policy=policy.SMTP) if isinstance(inner, Message) else b""
        _add_attachment(out, limits, filename or "message.eml", ctype, content, part, disposition)
        return
    is_text = ctype in ("text/plain", "text/html")
    if is_text and disposition != "attachment" and not filename:
        text = _decode_text(part)
        if ctype == "text/plain" and out.body_text is None:
            out.body_text = text
        elif ctype == "text/html" and out.body_html is None:
            out.body_html = text
        return
    payload_bytes = part.get_payload(decode=True)
    content = payload_bytes if isinstance(payload_bytes, bytes) else b""
    _add_attachment(out, limits, filename, ctype, content, part, disposition)


def _add_attachment(
    out: ParsedMessage,
    limits: MimeLimits,
    filename: str | None,
    ctype: str,
    content: bytes,
    part: Message,
    disposition: str,
) -> None:
    name = sanitize_filename(filename)
    ext = PurePosixPath(name.lower()).suffix
    if ctype in limits.denied_mime_types or ext in limits.denied_extensions:
        raise InvalidRequest(
            f"attachment type not allowed: {name} ({ctype})",
            details={"filename": name, "mime_type": ctype},
        )
    if len(content) > limits.max_attachment_bytes:
        raise InvalidRequest(
            f"attachment too large: {name}",
            details={"filename": name, "size": len(content)},
        )
    if len(out.attachments) >= limits.max_attachments:
        raise InvalidRequest("too many attachments", details={"max": limits.max_attachments})
    cid = part.get("Content-ID")
    out.attachments.append(
        ParsedAttachment(
            filename=name,
            mime_type=ctype,
            content=content,
            inline=disposition == "inline",
            content_id=_msgids(str(cid))[0] if cid and _msgids(str(cid)) else None,
        )
    )


def parse_mime(raw: bytes, limits: MimeLimits | None = None) -> ParsedMessage:
    limits = limits or MimeLimits()
    if not raw.strip():
        raise InvalidRequest("empty message")
    if len(raw) > limits.max_message_bytes:
        raise InvalidRequest("message too large", details={"size": len(raw)})
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except RecursionError:
        raise InvalidRequest("MIME structure too deeply nested") from None

    warnings: list[str] = []
    ids = _msgids(str(msg.get("Message-ID", "")))
    if not ids:
        warnings.append("missing Message-ID")
    in_reply = _msgids(str(msg.get("In-Reply-To", "")))
    from_list = _addresses(msg, "From")
    if not from_list:
        warnings.append("missing From")
    date: datetime | None = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date = parsedate_to_datetime(str(raw_date))
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            date = date.astimezone(UTC)
        except (TypeError, ValueError):
            warnings.append("unparseable Date")
    parsed = ParsedMessage(
        message_id=ids[0] if ids else None,
        in_reply_to=in_reply[0] if in_reply else None,
        references=_msgids(" ".join(str(v) for v in msg.get_all("References", []))),
        from_=from_list[0] if from_list else None,
        to=_addresses(msg, "To"),
        cc=_addresses(msg, "Cc"),
        bcc=_addresses(msg, "Bcc"),
        subject=str(msg.get("Subject", "")),
        body_text=None,
        body_html=None,
        date=date,
        headers=[(str(k), str(v)) for k, v in msg.items()],
        warnings=warnings,
    )
    try:
        _walk(msg, parsed, limits)
    except RecursionError:
        raise InvalidRequest("MIME structure too deeply nested") from None
    return parsed
