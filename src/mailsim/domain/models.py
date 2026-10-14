"""Canonical, provider-neutral value objects (spec 4, 4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Address(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str
    name: str | None = None


class AttachmentRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    filename: str
    mime_type: str
    size: int
    checksum: str  # sha256 hex of the content
    blob_ref: str  # content-addressed blob key
    inline: bool = False
    content_id: str | None = None


class CanonicalMessage(BaseModel):
    """Conceptual baseline from spec 4.2, extended with flags and separate timestamps."""

    id: str
    mailbox_id: str
    message_id_header: str
    thread_id: str
    folder: str
    from_: Address = Field(alias="from")
    to: list[Address] = Field(default_factory=list)
    cc: list[Address] = Field(default_factory=list)
    bcc: list[Address] = Field(default_factory=list)
    subject: str = ""
    body_text: str | None = None
    body_html: str | None = None
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    created_at: datetime
    received_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    headers: dict[str, Any] = Field(default_factory=dict)
    is_draft: bool = False
    raw_blob_ref: str | None = None

    model_config = ConfigDict(populate_by_name=True)


SYSTEM_FOLDERS: tuple[tuple[str, str], ...] = (
    ("INBOX", "inbox"),
    ("Sent", "sent"),
    ("Drafts", "drafts"),
    ("Trash", "trash"),
    ("Archive", "archive"),
)

VALID_FLAGS = frozenset({"SEEN", "ANSWERED", "FLAGGED", "DELETED", "DRAFT"})
