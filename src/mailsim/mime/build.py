"""Build deterministic RFC 5322 messages (no wall-clock, no random boundaries)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

from mailsim.domain.models import Address
from mailsim.mime.parse import ParsedAttachment


def _fmt(addr: Address) -> str:
    return f"{addr.name} <{addr.email}>" if addr.name else addr.email


def build_mime(
    *,
    message_id: str,
    from_: Address,
    to: list[Address],
    cc: list[Address] | None = None,
    subject: str,
    date: datetime,
    text: str | None = None,
    html: str | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    attachments: list[ParsedAttachment] | None = None,
) -> bytes:
    """Bcc is intentionally never written to the wire format."""
    msg = EmailMessage(policy=policy.SMTP)
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(date)
    msg["From"] = _fmt(from_)
    if to:
        msg["To"] = ", ".join(_fmt(a) for a in to)
    if cc:
        msg["Cc"] = ", ".join(_fmt(a) for a in cc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    msg.set_content(text if text is not None else "")
    if html is not None:
        msg.add_alternative(html, subtype="html")
    for att in attachments or []:
        maintype, _, subtype = att.mime_type.partition("/")
        msg.add_attachment(
            att.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=att.filename,
            disposition="inline" if att.inline else "attachment",
            cid=att.content_id,
        )
    raw = msg.as_bytes()
    # Deterministic multipart boundaries: replace stdlib's random ones with content hashes.
    return _stabilise_boundaries(raw)


def _stabilise_boundaries(raw: bytes) -> bytes:
    import re

    boundaries = list(dict.fromkeys(re.findall(rb'boundary="([^"]+)"', raw)))
    for idx, b in enumerate(boundaries):
        stable = (
            b"====="
            + hashlib.sha256(raw.replace(b, b"X") + bytes([idx])).hexdigest()[:24].encode()
            + b"=="
        )
        raw = raw.replace(b, stable)
    return raw
