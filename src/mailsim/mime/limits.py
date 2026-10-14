"""Attachment and message size/type limits (spec section 10: attachment safety)."""

from __future__ import annotations

from dataclasses import dataclass

DENIED_MIME_TYPES = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-dosexec",
        "application/x-sh",
        "application/x-csh",
        "application/x-executable",
        "application/vnd.microsoft.portable-executable",
        "application/x-msi",
    }
)
DENIED_EXTENSIONS = frozenset(
    {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".sh", ".ps1", ".vbs", ".jar"}
)


@dataclass(frozen=True)
class MimeLimits:
    max_attachment_bytes: int = 10 * 1024 * 1024
    max_message_bytes: int = 25 * 1024 * 1024
    max_attachments: int = 50
    denied_mime_types: frozenset[str] = DENIED_MIME_TYPES
    denied_extensions: frozenset[str] = DENIED_EXTENSIONS
