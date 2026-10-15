"""Canonical state -> Graph v1.0 resource JSON (message, mailFolder, fileAttachment, user).

Identifier semantics (spec 4.1 / 5.5):
- message id: `provider_ids["graph_message_id"]`; for a draft, the stable
  `provider_ids["graph_draft_id"]` (the engine replaces a draft's internal message on every
  update, the Graph id must survive PATCH).
- conversationId: `provider_ids["graph_thread_id"]`.
- mailFolder id: derived from (mailbox, system folder); well-known names resolve too.
- attachment id: derived from the internal attachment id.
All of them are opaque, deterministic and never equal to an internal id or a Message-ID.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from mailsim.adapters.graph.odata import Expand, normalise_extended_id
from mailsim.domain.models import SYSTEM_FOLDERS, Address, AttachmentRef, CanonicalMessage
from mailsim.providers.ids import graph_id

# well-known Graph folder name -> engine system folder type
WELL_KNOWN: dict[str, str] = {
    "inbox": "inbox",
    "sentitems": "sent",
    "drafts": "drafts",
    "deleteditems": "trash",
    "archive": "archive",
}
WELL_KNOWN_BY_TYPE = {v: k for k, v in WELL_KNOWN.items()}
DISPLAY_NAMES = {"inbox": "Inbox", "sent": "Sent Items", "drafts": "Drafts",
                 "trash": "Deleted Items", "archive": "Archive"}  # fmt: skip
TYPE_BY_NAME = {name: st for name, st in SYSTEM_FOLDERS}
# PidTagLastVerbExecuted: Outlook's "replied" marker; mapped to the ANSWERED flag
LAST_VERB_ID = "Integer 0x1081"
LAST_VERB_KEY = normalise_extended_id(LAST_VERB_ID)
VERB_REPLIED = {"102", "103"}  # EXCHIVERB_REPLYTOSENDER, EXCHIVERB_REPLYTOALL
PREVIEW_CHARS = 255
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def folder_id(mailbox_id: str, key: str) -> str:
    """Graph id of a folder; `key` is the system type (or `name:<folder name>`)."""
    return graph_id(f"folder:{mailbox_id}:{key}")


def root_folder_id(mailbox_id: str) -> str:
    return graph_id(f"folder:{mailbox_id}:msgfolderroot")


def user_id(mailbox_id: str) -> str:
    return graph_id(f"user:{mailbox_id}")


def folder_key(name: str, system_type: str | None = None) -> str:
    st = system_type or TYPE_BY_NAME.get(name)
    return st if st else f"name:{name}"


def message_graph_id(m: CanonicalMessage) -> str:
    if m.is_draft and m.provider_ids.get("graph_draft_id"):
        return m.provider_ids["graph_draft_id"]
    return m.provider_ids.get("graph_message_id") or graph_id(m.id)


def conversation_id(m: CanonicalMessage) -> str:
    return m.provider_ids.get("graph_thread_id") or graph_id(m.thread_id)


def attachment_id(att: AttachmentRef) -> str:
    return graph_id(f"attachment:{att.id}")


def html_to_text(markup: str) -> str:
    return html.unescape(_TAGS.sub(" ", markup))


def text_to_html(text: str) -> str:
    return f"<html><body><pre>{html.escape(text)}</pre></body></html>"


def user_categories(m: CanonicalMessage) -> list[str]:
    """User labels only: the engine adds the folder's system label first and UNREAD last."""
    labels = list(m.labels)
    system = TYPE_BY_NAME.get(m.folder)
    if system and labels and labels[0] == system.upper():
        labels = labels[1:]
    if labels and labels[-1] == "UNREAD" and "SEEN" not in m.flags and not m.is_draft:
        labels = labels[:-1]
    return labels


def recipient(a: Address) -> dict[str, Any]:
    return {"emailAddress": {"name": a.name or a.email, "address": a.email}}


def _unwire(content: str) -> str:
    """MIME serialisation terminates a body part with a newline; Graph returns the body as
    written, so one trailing newline is dropped."""
    return content[:-1] if content.endswith("\n") else content


def body(m: CanonicalMessage, prefer: str | None) -> dict[str, str]:
    """Graph returns HTML when the message has an HTML part, unless the client asked for
    `Prefer: outlook.body-content-type="text"` (and vice versa)."""
    text = _unwire(m.body_text) if m.body_text is not None else None
    markup = _unwire(m.body_html) if m.body_html is not None else None
    if prefer == "text":
        return {"contentType": "text",
                "content": text if text is not None else html_to_text(markup or "")}  # fmt: skip
    if prefer == "html" or markup is not None:
        return {"contentType": "html",
                "content": markup if markup is not None else text_to_html(text or "")}  # fmt: skip
    return {"contentType": "text", "content": text or ""}


def preview(m: CanonicalMessage) -> str:
    text = m.body_text if m.body_text is not None else html_to_text(m.body_html or "")
    return _SPACE.sub(" ", text).strip()[:PREVIEW_CHARS]


def change_key(m: CanonicalMessage) -> str:
    state = "|".join([m.id, m.folder, ",".join(m.flags), ",".join(m.labels), m.subject,
                      m.body_text or "", m.body_html or ""])  # fmt: skip
    return base64.b64encode(hashlib.sha256(state.encode()).digest()[:15]).decode()


def headers(m: CanonicalMessage) -> list[dict[str, str]]:
    out = []
    for name, values in m.headers.items():
        for value in values if isinstance(values, list) else [values]:
            out.append({"name": str(name), "value": str(value)})
    return out


def message(m: CanonicalMessage, *, select: frozenset[str] | None = None,
            prefer_body: str | None = None, expand: Expand | None = None,
            attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """Render a message. With `select`, only those properties (plus id and etag)."""
    received = m.received_at or m.created_at
    key = change_key(m)
    sender = recipient(m.from_)
    doc: dict[str, Any] = {
        "@odata.etag": f'W/"{key}"',
        "id": message_graph_id(m),
        "createdDateTime": iso(m.created_at),
        "lastModifiedDateTime": iso(m.created_at),
        "changeKey": key,
        "categories": user_categories(m),
        "receivedDateTime": iso(received),
        "sentDateTime": None if m.is_draft else iso(m.created_at),
        "hasAttachments": any(not a.inline for a in m.attachments),
        "internetMessageId": m.message_id_header,
        "subject": m.subject,
        "bodyPreview": preview(m),
        "importance": "normal",
        "parentFolderId": folder_id(m.mailbox_id, folder_key(m.folder)),
        "conversationId": conversation_id(m),
        "isDeliveryReceiptRequested": False,
        "isReadReceiptRequested": False,
        "isRead": "SEEN" in m.flags,
        "isDraft": m.is_draft,
        "inferenceClassification": "focused",
        "body": body(m, prefer_body),
        "sender": sender,
        "from": sender,
        "toRecipients": [recipient(a) for a in m.to],
        "ccRecipients": [recipient(a) for a in m.cc],
        "bccRecipients": [recipient(a) for a in m.bcc],
        "replyTo": [],
        "flag": {"flagStatus": "flagged" if "FLAGGED" in m.flags else "notFlagged"},
    }
    if select is not None:
        doc = {k: v for k, v in doc.items() if k in select or k in ("@odata.etag", "id")}
        if "internetMessageHeaders" in select:
            doc["internetMessageHeaders"] = headers(m)
    if expand is not None and expand.attachments:
        doc["attachments"] = attachments or []
    if expand is not None and expand.extended_ids:
        props = []
        if LAST_VERB_KEY in expand.extended_ids and "ANSWERED" in m.flags:
            props.append({"id": LAST_VERB_ID, "value": "102"})
        doc["singleValueExtendedProperties"] = props
    return doc


def filter_view(m: CanonicalMessage) -> dict[str, Any]:
    """The document $filter / $orderby are evaluated against (all filterable properties)."""
    return message(m)


def attachment(att: AttachmentRef, m: CanonicalMessage, content: bytes | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "@odata.mediaContentType": att.mime_type,
        "id": attachment_id(att),
        "lastModifiedDateTime": iso(m.created_at),
        "name": att.filename,
        "contentType": att.mime_type,
        "size": att.size,
        "isInline": att.inline,
        "contentId": att.content_id,
        "contentLocation": None,
    }
    if content is not None:
        doc["contentBytes"] = base64.b64encode(content).decode()
    return doc


def mail_folder(mailbox_id: str, f: Mapping[str, Any], counts: Mapping[str, tuple[int, int]]) -> dict[str, Any]:  # fmt: skip
    """`counts`: folder name -> (total, unread)."""
    total, unread = counts.get(f["name"], (0, 0))
    st = f["system_type"]
    return {
        "id": folder_id(mailbox_id, folder_key(f["name"], st)),
        "displayName": DISPLAY_NAMES.get(st, f["name"]) if st else f["name"],
        "parentFolderId": root_folder_id(mailbox_id),
        "childFolderCount": 0,
        "unreadItemCount": unread,
        "totalItemCount": total,
        "sizeInBytes": 0,
        "isHidden": False,
    }


def folder_counts(messages: Iterable[CanonicalMessage]) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    for m in messages:
        total, unread = counts.get(m.folder, (0, 0))
        counts[m.folder] = (total + 1, unread + ("SEEN" not in m.flags))
    return counts


def user(mailbox_id: str, email: str) -> dict[str, Any]:
    local = email.split("@", 1)[0]
    return {"id": user_id(mailbox_id), "displayName": local, "mail": email,
            "userPrincipalName": email}  # fmt: skip
