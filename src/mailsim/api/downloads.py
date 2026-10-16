"""Headers for serving recorded (untrusted) bytes: always a download, never rendered.

Used by every route that returns stored blobs (run artifacts, evaluation reports, agent and
operator attachment downloads, Graph attachment `$value`). Only the validated main type of a
small allowlist of passive types is echoed; anything that a browser could execute or render
as a document (HTML, SVG, XML, JavaScript, ...) is served as application/octet-stream.
"""

from __future__ import annotations

import re
from urllib.parse import quote

OCTET_STREAM = "application/octet-stream"
_MIME_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
MAX_FILENAME_CHARS = 150
PASSIVE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff",
    "image/avif", "image/heic",
    "application/pdf", "text/plain", "text/csv", "text/markdown", "application/json",
    "message/rfc822", OCTET_STREAM, "application/zip", "application/x-zip-compressed",
    "application/gzip", "application/msword", "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint", "application/rtf",
})  # fmt: skip
_OFFICE_XML = "application/vnd.openxmlformats-officedocument."
_OPEN_DOCUMENT = "application/vnd.oasis.opendocument."


def safe_content_type(content_type: str | None) -> str:
    """The main type (parameters stripped) when it is a known passive type, else
    application/octet-stream."""
    main = (content_type or "").split(";", 1)[0].strip().lower()
    if not _MIME_TYPE.fullmatch(main):
        return OCTET_STREAM
    if main in PASSIVE_TYPES or main.startswith((_OFFICE_XML, _OPEN_DOCUMENT)):
        return main
    return OCTET_STREAM


def content_disposition(filename: str) -> str:
    """`attachment` with an ASCII fallback `filename` (only [A-Za-z0-9._-], no leading dots)
    and, when that differs from the real name, the exact UTF-8 name as RFC 5987 `filename*`
    (percent-encoded: control characters, quotes and separators never reach the header raw)."""
    name = filename[:MAX_FILENAME_CHARS]
    fallback = _UNSAFE_FILENAME.sub("_", name).strip("._") or "download"
    disposition = f'attachment; filename="{fallback}"'
    if name and fallback != name:
        disposition += f"; filename*=UTF-8''{quote(name, safe='')}"
    return disposition


def safe_download_headers(filename: str, content_type: str | None) -> dict[str, str]:
    """Content-Type (see `safe_content_type`), attachment disposition (see
    `content_disposition`), nosniff, a sandboxing CSP and no caching."""
    return {"Content-Type": safe_content_type(content_type),
            "Content-Disposition": content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store"}  # fmt: skip


__all__ = ["OCTET_STREAM", "PASSIVE_TYPES", "content_disposition", "safe_content_type",
           "safe_download_headers"]  # fmt: skip
