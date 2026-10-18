"""Canonical, provider-neutral value objects (spec 4, 4.2)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mailsim.domain.errors import InvalidRequest, PermissionDenied


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
    imap_uid: int | None = None  # UID in the message's current folder
    provider_ids: dict[str, str] = Field(default_factory=dict)  # e.g. gmail_message_id, draft_id

    model_config = ConfigDict(populate_by_name=True)


SYSTEM_FOLDERS: tuple[tuple[str, str], ...] = (
    ("INBOX", "inbox"),
    ("Sent", "sent"),
    ("Drafts", "drafts"),
    ("Trash", "trash"),
    ("Archive", "archive"),
)

VALID_FLAGS = frozenset({"SEEN", "ANSWERED", "FLAGGED", "DELETED", "DRAFT"})

# System label names, matched ignoring case. They are DERIVED from where a message is and how
# it came to be (folder, flags, a real send or draft), never stored labels: an agent may not
# add or remove them as labels, and grading never reads them from the stored label list.
# INBOX/SENT/DRAFTS/TRASH/ARCHIVE and UNREAD are the canonical view's markers; DRAFT, STARRED,
# ANSWERED and DELETED are the Gmail profile's system labels for the same facts.
SYSTEM_LABELS = frozenset({"inbox", "sent", "drafts", "draft", "trash", "archive", "unread",
                           "starred", "answered", "deleted"})  # fmt: skip


def is_system_label(name: str) -> bool:
    return name.strip().casefold() in SYSTEM_LABELS


def system_labels_in(names: Iterable[str]) -> list[str]:
    """The names in `names` that are system labels (in order, as given)."""
    return [n for n in names if is_system_label(n)]


def system_label_error(names: Iterable[str]) -> InvalidRequest | None:
    """The refusal for a label write naming system labels (None when it names none)."""
    bad = system_labels_in(names)
    if not bad:
        return None
    return InvalidRequest(
        f"system labels cannot be added or removed: {', '.join(repr(n) for n in bad)} "
        "(they follow the message's folder and flags)",
        details={"labels": bad},
    )


def refuse_system_labels(names: Iterable[str]) -> None:
    """INVALID_REQUEST when `names` contains a system label (see SYSTEM_LABELS)."""
    error = system_label_error(names)
    if error is not None:
        raise error


def draft_flag_error(
    is_draft: bool, add: Iterable[str], remove: Iterable[str]
) -> PermissionDenied | None:
    """DRAFT marks a real draft: the refusal for putting it on other mail (forged "drafted"
    evidence) or taking it off a draft. None when the change is allowed (or changes nothing)."""
    if "DRAFT" in add and not is_draft:
        return PermissionDenied("the DRAFT flag marks drafts only: it cannot be set on this "
                                "message", details={"flag": "DRAFT"})  # fmt: skip
    if "DRAFT" in remove and is_draft:
        return PermissionDenied("the DRAFT flag cannot be removed from a draft",
                                details={"flag": "DRAFT"})  # fmt: skip
    return None
