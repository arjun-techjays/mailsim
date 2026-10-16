"""Headers for serving stored bytes (attachments, artifacts) as downloads.

A file is always an `attachment` (never rendered inline), its media type is never sniffed,
and only passive types keep their media type: anything a browser could execute or render
actively (HTML, SVG, XML, JavaScript, ...) is served as `application/octet-stream`.
"""

from __future__ import annotations

import re
from urllib.parse import quote

OCTET_STREAM = "application/octet-stream"
PASSIVE_TYPES = frozenset({
    "application/octet-stream", "application/pdf", "application/json", "text/plain", "text/csv",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff",
    "image/avif", "image/heic",
    "application/zip", "application/x-zip-compressed", "application/gzip",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
})  # fmt: skip
_ASCII_SAFE = re.compile(r"[^A-Za-z0-9 !#$&+,.;=@^_`{}~()\[\]-]")


def safe_content_type(content_type: str | None) -> str:
    """The media type without parameters if it is on the passive allowlist, else
    `application/octet-stream`."""
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media if media in PASSIVE_TYPES else OCTET_STREAM


def content_disposition(filename: str) -> str:
    """`attachment` with an ASCII fallback `filename` and the exact UTF-8 name as `filename*`
    (RFC 6266 / RFC 5987). Control characters, quotes, backslashes and path separators never
    reach the header."""
    name = "".join(ch for ch in filename if ch.isprintable()).replace("/", "_").replace("\\", "_")
    name = name.strip().strip(".") or "attachment"
    fallback = _ASCII_SAFE.sub("_", name)[:150] or "attachment"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name[:255], safe='')}"


def safe_download_headers(filename: str, content_type: str | None) -> dict[str, str]:
    """Every header of a safe download: allowlisted Content-Type, attachment disposition,
    nosniff, a sandboxing CSP and no caching."""
    return {
        "Content-Type": safe_content_type(content_type),
        "Content-Disposition": content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, no-store",
    }


__all__ = ["PASSIVE_TYPES", "content_disposition", "safe_content_type", "safe_download_headers"]
