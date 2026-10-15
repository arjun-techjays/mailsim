"""Gmail label model over the canonical mailbox (folders + flags + message labels).

System labels are derived, never stored:

- folder -> label: inbox=INBOX, sent=SENT, drafts=DRAFT, trash=TRASH; archive has no folder
  label (Gmail's "archived" = not in INBOX).
- flags -> labels: not SEEN=UNREAD, FLAGGED=STARRED, and two sandbox extensions for IMAP flags
  that Gmail has no label for: ANSWERED=MAILSIM_ANSWERED, DELETED=MAILSIM_DELETED.

User labels are the canonical message labels (by name). Their ids are a deterministic,
reversible encoding of the name, ``Label_<hex(utf-8 name)>``, so they are stable across
restarts and adapters and need no registry to resolve. Renaming a label therefore changes its
id (a documented deviation: Gmail keeps the id).
"""

from __future__ import annotations

import binascii
import re
import threading
from typing import Any

from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.domain.models import SYSTEM_FOLDERS, CanonicalMessage

USER_PREFIX = "Label_"
FOLDER_LABEL = {"inbox": "INBOX", "sent": "SENT", "drafts": "DRAFT", "trash": "TRASH"}
LABEL_FOLDER = {v: k for k, v in FOLDER_LABEL.items()}
FLAG_LABEL = {"FLAGGED": "STARRED", "ANSWERED": "MAILSIM_ANSWERED", "DELETED": "MAILSIM_DELETED"}
LABEL_FLAG = {v: k for k, v in FLAG_LABEL.items()}
SYSTEM_LABELS = ("INBOX", "SENT", "DRAFT", "TRASH", "UNREAD", "STARRED", "MAILSIM_ANSWERED",
                 "MAILSIM_DELETED")  # fmt: skip
# Real Gmail system labels this profile does not model: modifying them is UNSUPPORTED, and a
# list filter on them matches nothing (there is no spam folder, no categories, no importance).
UNMODELLED_LABELS = frozenset({"SPAM", "IMPORTANT", "CHAT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
                               "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"})  # fmt: skip
RESERVED_NAMES = frozenset(n.casefold() for n in (*SYSTEM_LABELS, *UNMODELLED_LABELS, "DRAFTS"))
_FOLDER_TYPES = {name.lower(): st for name, st in SYSTEM_FOLDERS}


def user_label_id(name: str) -> str:
    return USER_PREFIX + name.encode().hex()


def user_label_name(label_id: str) -> str:
    """Decode a ``Label_...`` id. Anything malformed is an unknown label (404)."""
    try:
        return bytes.fromhex(label_id.removeprefix(USER_PREFIX)).decode()
    except (ValueError, binascii.Error):
        raise NotFound(f"Invalid label: {label_id}") from None


def is_user_label_id(label_id: str) -> bool:
    return label_id.startswith(USER_PREFIX) and len(label_id) > len(USER_PREFIX)


def check_label_id(label_id: str) -> str:
    """Validate a label id from a request. Returns 'system', 'unmodelled' or 'user'."""
    if label_id in SYSTEM_LABELS:
        return "system"
    if label_id in UNMODELLED_LABELS or label_id.startswith("CATEGORY_"):
        return "unmodelled"
    if is_user_label_id(label_id):
        user_label_name(label_id)
        return "user"
    raise NotFound(f"Invalid label: {label_id}")


def folder_type(msg: CanonicalMessage) -> str:
    """System type of the message's folder (inbox|sent|drafts|trash|archive)."""
    return _FOLDER_TYPES.get(msg.folder.lower()) or (msg.labels[0].lower() if msg.labels else "")


def user_labels(msg: CanonicalMessage) -> list[str]:
    """The message's own labels: canonical labels minus the derived folder / UNREAD markers."""
    labels = list(msg.labels)
    marker = folder_type(msg).upper()
    if labels[:1] == [marker]:
        labels = labels[1:]
    if "SEEN" not in msg.flags and not msg.is_draft and labels[-1:] == ["UNREAD"]:
        labels = labels[:-1]
    return labels


def is_unread(msg: CanonicalMessage) -> bool:
    return "SEEN" not in msg.flags and not msg.is_draft


def message_label_ids(msg: CanonicalMessage) -> list[str]:
    ids = [user_label_id(n) for n in dict.fromkeys(user_labels(msg))]
    folder_label = FOLDER_LABEL.get(folder_type(msg))
    if folder_label:
        ids.append(folder_label)
    if is_unread(msg):
        ids.append("UNREAD")
    ids += [FLAG_LABEL[f] for f in ("FLAGGED", "ANSWERED", "DELETED") if f in msg.flags]
    return ids


def check_user_label_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequest("Invalid label name: a non-empty name is required")
    if name.strip().casefold() in RESERVED_NAMES:
        raise InvalidRequest(f"Invalid label name: {name!r} is reserved for a system label")
    if len(name) > 225:
        raise InvalidRequest("Invalid label name: at most 225 characters")
    return name.strip()


SETTING_KEYS = ("messageListVisibility", "labelListVisibility", "color")
MESSAGE_VISIBILITY = frozenset({"show", "hide"})
LABEL_VISIBILITY = frozenset({"labelShow", "labelShowIfUnread", "labelHide"})
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
MAX_REGISTERED_LABELS = 1000
MAX_REGISTRY_KEYS = 256  # (run, mailbox) buckets kept; the least recently written is evicted


def check_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Strictly validated cosmetic settings from a labels.create/update/patch body."""
    out: dict[str, Any] = {}
    if "messageListVisibility" in body:
        v = body["messageListVisibility"]
        if not isinstance(v, str) or v not in MESSAGE_VISIBILITY:
            raise InvalidRequest("messageListVisibility must be one of show, hide")
        out["messageListVisibility"] = body["messageListVisibility"]
    if "labelListVisibility" in body:
        v = body["labelListVisibility"]
        if not isinstance(v, str) or v not in LABEL_VISIBILITY:
            raise InvalidRequest("labelListVisibility must be one of labelShow, "
                                 "labelShowIfUnread, labelHide")  # fmt: skip
        out["labelListVisibility"] = body["labelListVisibility"]
    if "color" in body:
        color = body["color"]
        if not (isinstance(color, dict) and set(color) == {"textColor", "backgroundColor"}
                and all(isinstance(v, str) and _HEX_COLOR.fullmatch(v) for v in color.values())):  # fmt: skip
            raise InvalidRequest("color must be {textColor, backgroundColor} as #rrggbb")
        out["color"] = {
            "textColor": color["textColor"],
            "backgroundColor": color["backgroundColor"],
        }
    return out


def system_label(label_id: str) -> dict[str, Any]:
    return {"id": label_id, "name": label_id, "type": "system",
            "messageListVisibility": "hide", "labelListVisibility": "labelHide"
            if label_id.startswith("MAILSIM_") else "labelShow"}  # fmt: skip


def user_label(name: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": user_label_id(name), "name": name, "type": "user",
                           "messageListVisibility": "show", "labelListVisibility": "labelShow"}  # fmt: skip
    out.update(settings or {})
    return out


class LabelRegistry:
    """Labels created through the Gmail API that are not (yet) on any message, plus the cosmetic
    settings (visibility, color) of every label.

    The engine stores labels only on messages, so this lives in the adapter process: it assumes
    a SINGLE server process, does not survive a restart, and `labels.create` is not an audited
    or budgeted engine write. Entries are kept per (run, mailbox) with the history id they were
    recorded at, so the owner can drop them when the run was reset since (`clear`); at most
    MAX_REGISTERED_LABELS per (run, mailbox) and at most MAX_REGISTRY_KEYS buckets (LRU by write).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._labels: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._since: dict[tuple[str, str], int] = {}

    def since(self, key: tuple[str, str]) -> int | None:
        with self._lock:
            return self._since.get(key) if self._labels.get(key) else None

    def get(self, key: tuple[str, str]) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {n: dict(s) for n, s in self._labels.get(key, {}).items()}

    def put(self, key: tuple[str, str], name: str, settings: dict[str, Any], since: int) -> None:
        with self._lock:
            if key not in self._labels and len(self._labels) >= MAX_REGISTRY_KEYS:
                oldest = next(iter(self._labels))  # finished runs age out (bounded memory)
                self._labels.pop(oldest)
                self._since.pop(oldest, None)
            bucket = self._labels.pop(key, {})
            self._labels[key] = bucket  # most recently written last
            if name not in bucket and len(bucket) >= MAX_REGISTERED_LABELS:
                raise InvalidRequest(f"too many labels (at most {MAX_REGISTERED_LABELS})")
            self._since.setdefault(key, since)
            bucket[name] = dict(settings)

    def drop(self, key: tuple[str, str], name: str) -> None:
        with self._lock:
            self._labels.get(key, {}).pop(name, None)

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._labels.pop(key, None)
            self._since.pop(key, None)
