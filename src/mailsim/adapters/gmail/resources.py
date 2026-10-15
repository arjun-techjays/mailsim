"""Canonical message -> Gmail resource (formats minimal | metadata | full | raw), MIME helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import Any

from mailsim.adapters.gmail.labels import message_label_ids
from mailsim.adapters.gmail.search import message_date
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import AttachmentRef, CanonicalMessage
from mailsim.providers.ids import gmail_id

FORMATS = ("minimal", "metadata", "full", "raw")
SNIPPET_CHARS = 200


def b64url(data: bytes) -> str:
    """URL-safe base64 WITH padding (what Gmail returns and urlsafe_b64decode expects)."""
    return base64.urlsafe_b64encode(data).decode()


def decode_raw(value: object) -> bytes:
    """Decode a request's ``raw`` field: base64url, padding optional, standard alphabet tolerated."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest("'raw' RFC822 payload message string or uploading message via "
                             "/upload/* URL required")  # fmt: skip
    text = value.strip().replace("+", "-").replace("/", "_")
    try:
        data = base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode())
    except (binascii.Error, ValueError):
        raise InvalidRequest("'raw' is not valid base64url") from None
    if not data.strip():
        raise InvalidRequest("'raw' decodes to an empty message")
    return data


def html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def snippet(msg: CanonicalMessage) -> str:
    text = msg.body_text if msg.body_text is not None else html_to_text(msg.body_html or "")
    return " ".join(text.split())[:SNIPPET_CHARS]


def gmail_message_id(msg: CanonicalMessage) -> str:
    return msg.provider_ids.get("gmail_message_id") or gmail_id(msg.id)


def gmail_thread_id(msg: CanonicalMessage) -> str:
    return msg.provider_ids.get("gmail_thread_id") or gmail_id(msg.thread_id)


def gmail_draft_id(msg: CanonicalMessage) -> str:
    return msg.provider_ids.get("gmail_draft_id") or gmail_id(
        msg.provider_ids.get("draft_id") or msg.id
    )


def stub(msg: CanonicalMessage) -> dict[str, Any]:
    return {"id": gmail_message_id(msg), "threadId": gmail_thread_id(msg)}


def internal_date(msg: CanonicalMessage) -> str:
    return str(int(message_date(msg).timestamp() * 1000))


def _is_body(part: Message) -> bool:
    disposition = (part.get_content_disposition() or "").lower()
    return (part.get_content_type() in ("text/plain", "text/html") and disposition != "attachment"
            and not part.get_filename())  # fmt: skip


def _leaf_bytes(part: Message) -> bytes:
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()
        first = inner[0] if isinstance(inner, list) and inner else None
        return first.as_bytes(policy=policy.SMTP) if isinstance(first, Message) else b""
    data = part.get_payload(decode=True)
    return data if isinstance(data, bytes) else b""


class _Attachments:
    """Match MIME leaves to canonical attachments (by content checksum, then filename)."""

    def __init__(self, refs: list[AttachmentRef]) -> None:
        self.free = list(refs)

    def take(self, content: bytes, filename: str) -> AttachmentRef | None:
        digest = hashlib.sha256(content).hexdigest()
        ref = next((a for a in self.free if a.checksum == digest), None) or next(
            (a for a in self.free if a.filename == filename), None
        )
        if ref is not None:
            self.free.remove(ref)
        return ref


def headers_of(part: Message) -> list[dict[str, str]]:
    return [{"name": str(k), "value": str(v)} for k, v in part.items()]


def payload(part: Message, part_id: str, atts: _Attachments) -> dict[str, Any]:
    out: dict[str, Any] = {"partId": part_id, "mimeType": part.get_content_type(),
                           "filename": part.get_filename() or "", "headers": headers_of(part)}  # fmt: skip
    if part.is_multipart() and part.get_content_type() != "message/rfc822":
        subs = list(part.iter_parts()) if isinstance(part, EmailMessage) else []
        out["body"] = {"size": 0}
        out["parts"] = [payload(sub, f"{part_id}.{i}" if part_id else str(i), atts)
                        for i, sub in enumerate(subs)]  # fmt: skip
        return out
    content = _leaf_bytes(part)
    ref = None if _is_body(part) else atts.take(content, out["filename"])
    if ref is not None:
        out["body"] = {"attachmentId": gmail_id(ref.id), "size": ref.size}
    else:
        out["body"] = {"size": len(content), "data": b64url(content)} if content else {"size": 0}
    return out


def parse_raw(raw: bytes) -> EmailMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(parsed, EmailMessage)
    return parsed


def message_resource(msg: CanonicalMessage, raw: bytes, fmt: str, history_id: str,
                     metadata_headers: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    if fmt not in FORMATS:
        raise InvalidRequest(f"Invalid format {fmt!r}: use one of {', '.join(FORMATS)}")
    out: dict[str, Any] = {**stub(msg), "labelIds": message_label_ids(msg), "snippet": snippet(msg),
                           "historyId": history_id, "internalDate": internal_date(msg),
                           "sizeEstimate": len(raw)}  # fmt: skip
    if fmt == "raw":
        out["raw"] = b64url(raw)
    elif fmt == "full":
        out["payload"] = payload(parse_raw(raw), "", _Attachments(list(msg.attachments)))
    elif fmt == "metadata":
        root = parse_raw(raw)
        wanted = {h.lower() for h in metadata_headers or []}
        headers = [h for h in headers_of(root) if not wanted or h["name"].lower() in wanted]
        out["payload"] = {"partId": "", "mimeType": root.get_content_type(), "filename": "",
                          "headers": headers}  # fmt: skip
    return out


def find_attachment(msg: CanonicalMessage, attachment_id: str) -> AttachmentRef | None:
    return next((a for a in msg.attachments if gmail_id(a.id) == attachment_id), None)
