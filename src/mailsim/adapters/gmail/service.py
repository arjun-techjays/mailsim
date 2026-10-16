"""Gmail semantics over one agent's `AgentMailbox` (the only engine surface used here).

Each request builds one `GmailService`. Reads go through the facade (audited, free); every
state change is one or more facade writes (each counted against the run's agent budget by the
engine). The mailbox address (``userId``, ``from:me``) and the run's virtual time
(``older_than:``/``newer_than:``) come from ``AgentMailbox.mailbox_info()``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from mailsim.adapters.gmail import labels as lb
from mailsim.adapters.gmail.resources import (
    b64url,
    find_attachment,
    gmail_draft_id,
    gmail_message_id,
    gmail_thread_id,
    html_to_text,
    message_resource,
    snippet,
    stub,
)
from mailsim.adapters.gmail.search import (
    Context,
    includes_trash,
    matches,
    message_date,
    parse_query,
    terms,
)
from mailsim.auth.principal import Principal
from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import (
    Conflict,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    UnsupportedOperation,
)
from mailsim.domain.models import Address, CanonicalMessage
from mailsim.engine.facades import AgentMailbox, DraftThreading
from mailsim.engine.pagination import decode_cursor, encode_cursor
from mailsim.mime.build import build_mime
from mailsim.mime.parse import ParsedMessage, parse_mime
from mailsim.providers.ids import gmail_id
from mailsim.store.blob import BlobStore

DEFAULT_PAGE = 100
MAX_PAGE = 500
LATEST_PROBE = 2**62  # list_changes() after this sequence returns only `latest_sequence`
ADDED_EVENTS = frozenset({"message.delivered", "message.sent", "message.inserted",
                          "draft.created", "message.copied"})  # fmt: skip
LABEL_EVENTS = frozenset({"message.labels_changed", "message.flags_changed", "message.moved"})
HISTORY_TYPES = frozenset({"messageAdded", "messageDeleted", "labelAdded", "labelRemoved"})
FOLDER_FOR_LABEL = {"INBOX": "inbox", "SENT": "sent", "DRAFT": "drafts", "TRASH": "trash"}
FOLDER_FOR_IN = {"inbox": "inbox", "sent": "sent", "drafts": "drafts", "draft": "drafts",
                 "trash": "trash", "archive": "archive"}  # fmt: skip
MAX_HISTORY_ID = 2**62


def page_size(max_results: int | None) -> int:
    if max_results is None:
        return DEFAULT_PAGE
    if max_results < 1:
        raise InvalidRequest("maxResults must be a positive integer")
    return min(max_results, MAX_PAGE)


def paginate[T](items: list[T], key: Callable[[T], tuple[datetime, str]], size: int,
                token: str | None) -> tuple[list[T], str | None]:  # fmt: skip
    """Newest first, keyset on (date, gmail id): stable under a fixed state."""
    ordered = sorted(items, key=key, reverse=True)
    if token:
        stamp, last = decode_cursor(token)
        bound = (parse_ts(stamp), last)
        ordered = [x for x in ordered if key(x) < bound]
    page = ordered[:size]
    if len(ordered) <= size:
        return page, None
    when, ident = key(page[-1])
    return page, encode_cursor(iso(when), ident)


def _msg_key(msg: CanonicalMessage) -> tuple[datetime, str]:
    return message_date(msg), gmail_message_id(msg)


def history_number(value: str | None, message: str) -> int:
    """A history id / sequence: ASCII digits only, bounded."""
    if value is None or not (0 < len(value) <= 19 and value.isascii() and value.isdigit()):
        raise InvalidRequest(message)
    number = int(value)
    if number > MAX_HISTORY_ID:
        raise InvalidRequest(message)
    return number


def history_record(
    change: dict[str, Any], msg: CanonicalMessage | None
) -> tuple[dict[str, Any], set[str]]:
    """One engine change -> (Gmail history record, history types it represents)."""
    mid = str(change["message_id"])
    ref: dict[str, Any] = {"id": gmail_id(mid)}
    if msg is not None:
        ref["threadId"] = gmail_thread_id(msg)
    record: dict[str, Any] = {"id": str(change["sequence"]), "messages": [dict(ref)]}
    kind = str(change["type"])
    if kind in ADDED_EVENTS:
        added = {**ref, "labelIds": lb.message_label_ids(msg)} if msg is not None else ref
        record["messagesAdded"] = [{"message": added}]
        return record, {"messageAdded"}
    if kind == "message.deleted":
        record["messagesDeleted"] = [{"message": {"id": ref["id"]}}]
        return record, {"messageDeleted"}
    if kind in LABEL_EVENTS:  # the change feed carries no label delta: refetch the message
        return record, {"labelAdded", "labelRemoved"}
    return record, set()


class GmailService:
    def __init__(self, mb: AgentMailbox, principal: Principal, blobs: BlobStore,
                 registry: lb.LabelRegistry) -> None:  # fmt: skip
        self.mb, self.principal = mb, principal
        self._blobs, self._registry = blobs, registry
        self._listings: dict[str | None, list[CanonicalMessage]] = {}
        self._facts: tuple[str, datetime] | None = None
        self._history: str | None = None

    # ------------------------------------------------------------------- identity / facts

    def _run_facts(self) -> tuple[str, datetime]:
        if self._facts is None:
            info = self.mb.mailbox_info()
            self._facts = (str(info["email"]), parse_ts(info["virtual_time"]))
        return self._facts

    def email(self) -> str:
        return self._run_facts()[0]

    def now(self) -> datetime:
        return self._run_facts()[1]

    def check_user(self, user_id: str) -> None:
        if user_id != "me" and user_id.lower() != self.email().lower():
            raise PermissionDenied(f"Delegation denied for {user_id}")

    def history_id(self) -> str:
        if self._history is None:
            self._history = str(self.mb.list_changes(LATEST_PROBE, limit=1)["latest_sequence"])
        return self._history

    def _changed(self) -> None:
        self._listings, self._history = {}, None

    # ------------------------------------------------------------------------- lookups

    def listing(self, folder: str | None = None) -> list[CanonicalMessage]:
        """Every message (of one folder), oldest first, through the facade's paged listing."""
        if folder not in self._listings:
            out: list[CanonicalMessage] = []
            cursor: str | None = None
            while True:
                page = self.mb.list_messages(folder=folder, page_size=MAX_PAGE, cursor=cursor)
                out += page.items
                cursor = page.next_cursor
                if cursor is None:
                    break
            self._listings[folder] = out
        return self._listings[folder]

    def all_messages(self) -> list[CanonicalMessage]:
        return self.listing(None)

    def message(self, gid: str) -> CanonicalMessage:
        return self.mb.get_message(self.mb.resolve_external_id("gmail", "message", gid))

    def thread(self, gtid: str) -> list[CanonicalMessage]:
        return self.mb.get_thread(self.mb.resolve_external_id("gmail", "thread", gtid))

    def draft(self, gdid: str) -> CanonicalMessage:
        return self.mb.get_message(self.mb.resolve_external_id("gmail", "draft", gdid))

    def raw(self, msg: CanonicalMessage) -> bytes:
        if msg.raw_blob_ref:
            return self._blobs.get(msg.raw_blob_ref)
        return build_mime(message_id=msg.message_id_header, from_=msg.from_, to=msg.to, cc=msg.cc,
                          subject=msg.subject, date=msg.created_at, text=msg.body_text,
                          html=msg.body_html, in_reply_to=msg.in_reply_to,
                          references=msg.references or None)  # fmt: skip

    def attachment(self, gid: str, attachment_id: str) -> dict[str, Any]:
        msg = self.message(gid)
        ref = find_attachment(msg, attachment_id)
        if ref is None:
            raise NotFound(f"attachment {attachment_id} not found")
        data = self._blobs.get(ref.blob_ref)
        return {"attachmentId": attachment_id, "size": len(data), "data": b64url(data)}

    def resource(self, msg: CanonicalMessage, fmt: str = "full",
                 metadata_headers: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
        return message_resource(msg, self.raw(msg), fmt, self.history_id(), metadata_headers)

    # ------------------------------------------------------------------------- search

    @staticmethod
    def _folder_filter(node: Any, label_ids: list[str]) -> str | None:
        """A single folder the result must lie in, pushed down to the engine listing."""
        folders = {FOLDER_FOR_LABEL[x] for x in label_ids if x in FOLDER_FOR_LABEL}
        folders |= {FOLDER_FOR_IN[t.value.lower()] for t, top in terms(node)
                    if top and t.op == "in" and t.value.lower() in FOLDER_FOR_IN}  # fmt: skip
        return folders.pop() if len(folders) == 1 else None

    def _candidates(self, node: Any, folder: str | None) -> list[CanonicalMessage]:
        """Free text goes through the engine search (guarded + audited as `search`); when a
        positive top-level phrase exists its hits are the candidate set, else the listing of
        the single folder the filters require (or everything)."""
        free = [(t, top) for t, top in terms(node) if t.op is None]
        if not free:
            return self.listing(folder)
        term, top = next(((t, tp) for t, tp in free if tp), free[0])
        hits = self.mb.search_messages(term.value)
        return hits if top else self.listing(folder)

    def select(
        self, q: str | None, label_ids: list[str], include_spam_trash: bool
    ) -> list[CanonicalMessage]:
        node = parse_query(q or "")
        for label_id in label_ids:
            lb.check_label_id(label_id)
        keep_trash = include_spam_trash or includes_trash(node) or "TRASH" in label_ids
        ctx = Context(self.email, self.now)
        out = []
        for msg in self._candidates(node, self._folder_filter(node, label_ids)):
            if not keep_trash and lb.folder_type(msg) == "trash":
                continue
            ids = lb.message_label_ids(msg)
            if all(x in ids for x in label_ids) and matches(node, msg, ctx):
                out.append(msg)
        return out

    def list_messages(self, q: str | None, label_ids: list[str], max_results: int | None,
                      token: str | None, include_spam_trash: bool) -> dict[str, Any]:  # fmt: skip
        size = page_size(max_results)
        hits = self.select(q, label_ids, include_spam_trash)
        page, nxt = paginate(hits, _msg_key, size, token)
        out: dict[str, Any] = {"resultSizeEstimate": len(hits)}
        if page:
            out["messages"] = [stub(x) for x in page]
        if nxt:
            out["nextPageToken"] = nxt
        return out

    def list_threads(self, q: str | None, label_ids: list[str], max_results: int | None,
                     token: str | None, include_spam_trash: bool) -> dict[str, Any]:  # fmt: skip
        size = page_size(max_results)
        latest: dict[str, CanonicalMessage] = {}
        for msg in self.select(q, label_ids, include_spam_trash):
            tid = gmail_thread_id(msg)
            latest[tid] = max(latest.get(tid, msg), msg, key=_msg_key)
        items = list(latest.items())
        page, nxt = paginate(items, lambda kv: (message_date(kv[1]), kv[0]), size, token)
        out: dict[str, Any] = {"resultSizeEstimate": len(items)}
        if page:
            hid = self.history_id()
            out["threads"] = [
                {"id": tid, "snippet": snippet(msg), "historyId": hid} for tid, msg in page
            ]
        if nxt:
            out["nextPageToken"] = nxt
        return out

    def get_thread(self, gtid: str, fmt: str, metadata_headers: list[str] | None) -> dict[str, Any]:
        msgs = self.thread(gtid)
        return {"id": gtid, "historyId": self.history_id(),
                "messages": [self.resource(x, fmt, metadata_headers) for x in msgs]}  # fmt: skip

    def profile(self) -> dict[str, Any]:
        msgs = self.all_messages()
        return {"emailAddress": self.email(), "messagesTotal": len(msgs),
                "threadsTotal": len({x.thread_id for x in msgs}), "historyId": self.history_id()}  # fmt: skip

    # ------------------------------------------------------------------------ modify

    def _target_folder(
        self, msg: CanonicalMessage, add: list[str], remove: list[str]
    ) -> str | None:
        current = lb.folder_type(msg)
        wanted = [lb.LABEL_FOLDER[x] for x in add if x in ("INBOX", "SENT", "TRASH")]
        if len(wanted) > 1:
            raise InvalidRequest("Invalid label combination: a message is in exactly one of "
                                 "INBOX, SENT, TRASH or archived")  # fmt: skip
        if wanted:
            return None if wanted[0] == current else wanted[0]
        if lb.FOLDER_LABEL.get(current) in remove:
            return "untrash" if current == "trash" else "archive"
        return None

    def modify(self, msg: CanonicalMessage, add: list[str], remove: list[str]) -> CanonicalMessage:
        """Apply label changes as the minimal set of engine writes, in this order: folder move,
        user labels, flags. Labels already in the requested state cost nothing."""
        add, remove = list(dict.fromkeys(add)), list(dict.fromkeys(remove))
        if set(add) & set(remove):
            raise InvalidRequest("a label cannot be both added and removed")
        for label_id in (*add, *remove):
            if lb.check_label_id(label_id) == "unmodelled":
                raise UnsupportedOperation(f"label {label_id} is not modelled by this sandbox")
        if "DRAFT" in add or "DRAFT" in remove:
            raise InvalidRequest("Invalid label: DRAFT changes only through the drafts API")
        folder = self._target_folder(msg, add, remove)
        own = set(lb.user_labels(msg))
        add_names = [n for n in (lb.user_label_name(x) for x in add if lb.is_user_label_id(x))
                     if n not in own]  # fmt: skip
        remove_names = [n for n in (lb.user_label_name(x) for x in remove if lb.is_user_label_id(x))
                        if n in own]  # fmt: skip
        flags = set(msg.flags)
        flag_add = [
            lb.LABEL_FLAG[x] for x in add if x in lb.LABEL_FLAG and lb.LABEL_FLAG[x] not in flags
        ]
        flag_rm = [
            lb.LABEL_FLAG[x] for x in remove if x in lb.LABEL_FLAG and lb.LABEL_FLAG[x] in flags
        ]
        if "UNREAD" in add and "SEEN" in flags:
            flag_rm.append("SEEN")
        if "UNREAD" in remove and "SEEN" not in flags:
            flag_add.append("SEEN")
        if folder == "trash":
            self.mb.trash(msg.id)
        elif folder == "untrash":
            self.mb.untrash(msg.id)
        elif folder is not None:
            self.mb.move(msg.id, folder)
        if add_names or remove_names:
            self.mb.modify_labels(msg.id, add=add_names, remove=remove_names)
        if flag_add or flag_rm:
            self.mb.set_flags(msg.id, add=flag_add, remove=flag_rm)
        self._changed()
        return self.mb.get_message(msg.id)

    def trash(self, msg: CanonicalMessage, restore: bool = False) -> CanonicalMessage:
        """Trash / untrash; a message already in the requested state costs no write."""
        if (lb.folder_type(msg) == "trash") == restore:
            (self.mb.untrash if restore else self.mb.trash)(msg.id)
            self._changed()
            return self.mb.get_message(msg.id)
        return msg

    def delete(self, msg: CanonicalMessage) -> None:
        self.mb.delete(msg.id)
        self._changed()

    # -------------------------------------------------------------------- send/insert

    @staticmethod
    def _parse(raw: bytes) -> tuple[ParsedMessage, str]:
        parsed = parse_mime(raw)
        if parsed.attachments:
            raise UnsupportedOperation("attachments are not supported when composing through this "
                                       "sandbox profile (send, drafts)")  # fmt: skip
        text = (
            parsed.body_text
            if parsed.body_text is not None
            else html_to_text(parsed.body_html or "")
        )
        return parsed, text

    @staticmethod
    def _emails(addrs: list[Address]) -> list[str]:
        return list(dict.fromkeys(a.email.casefold() for a in addrs))

    def _check_thread(self, thread_id: str | None) -> None:
        if thread_id:
            self.mb.resolve_external_id("gmail", "thread", thread_id)

    def _reply_target(self, parsed: ParsedMessage) -> CanonicalMessage | None:
        """In-Reply-To first, then References newest to oldest: the first one that names a
        (non-draft) message of this mailbox; received copies win over sent ones."""
        refs = [r for r in (parsed.in_reply_to, *reversed(parsed.references)) if r]
        if not refs:
            return None
        by_header: dict[str, list[CanonicalMessage]] = {}
        for x in self.all_messages():
            if not x.is_draft and x.message_id_header:
                by_header.setdefault(x.message_id_header, []).append(x)
        for ref in refs:
            if ref in by_header:
                return min(by_header[ref], key=lambda x: lb.folder_type(x) == "sent")
        return None

    def send(self, raw: bytes, thread_id: str | None) -> CanonicalMessage:
        """Gmail `send`. A message whose In-Reply-To (or last References id) names a message of
        the mailbox is ONE engine `reply` with the composed recipients, subject and body as
        overrides (policy, budget, ANSWERED, send_reply triggers); anything else is `send`."""
        parsed, text = self._parse(raw)
        self._check_thread(thread_id)
        to, cc, bcc = (self._emails(parsed.to), self._emails(parsed.cc), self._emails(parsed.bcc))
        orig = self._reply_target(parsed)
        if orig is not None:
            result = self.mb.reply(orig.id, text=text, to=to, cc=cc, bcc=bcc,
                                   subject=parsed.subject, html=parsed.body_html)  # fmt: skip
        else:
            result = self.mb.send(to=to, subject=parsed.subject, text=text, cc=cc, bcc=bcc,
                                  html=parsed.body_html)  # fmt: skip
        self._changed()
        return self.mb.get_message(result["message_id"])

    def insert(self, raw: bytes, label_ids: list[str]) -> CanonicalMessage:
        """Gmail `insert`/`import`: place without sending. Labels decide the folder (TRASH >
        INBOX > SENT > archived), the read state (no UNREAD = read) and stars/user labels; each
        of those beyond the insert itself is a separate write."""
        for label_id in label_ids:
            if lb.check_label_id(label_id) == "unmodelled":
                raise UnsupportedOperation(f"label {label_id} is not modelled by this sandbox")
        if "DRAFT" in label_ids:
            raise InvalidRequest("Invalid label: DRAFT (create drafts through the drafts API)")
        folder = next((lb.LABEL_FOLDER[x] for x in ("TRASH", "INBOX", "SENT") if x in label_ids),
                      "archive")  # fmt: skip
        mid = str(self.mb.insert_message(raw, folder=folder)["message_id"])
        flags = ([] if "UNREAD" in label_ids else ["SEEN"]) + [lb.LABEL_FLAG[x] for x in label_ids
                                                               if x in lb.LABEL_FLAG]  # fmt: skip
        if flags:
            self.mb.set_flags(mid, add=flags)
        names = [lb.user_label_name(x) for x in label_ids if lb.is_user_label_id(x)]
        if names:
            self.mb.modify_labels(mid, add=names)
        self._changed()
        return self.mb.get_message(mid)

    # -------------------------------------------------------------------------- drafts

    def _draft_content(self, raw: bytes) -> tuple[ParsedMessage, dict[str, Any]]:
        parsed, text = self._parse(raw)
        return parsed, {"to": self._emails(parsed.to), "subject": parsed.subject, "text": text,
                        "cc": self._emails(parsed.cc), "bcc": self._emails(parsed.bcc),
                        "html": parsed.body_html}  # fmt: skip

    def draft_resource(self, msg: CanonicalMessage, fmt: str | None = None,
                       metadata_headers: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
        body = (self.resource(msg, fmt, metadata_headers) if fmt
                else {**stub(msg), "labelIds": lb.message_label_ids(msg)})  # fmt: skip
        return {"id": gmail_draft_id(msg), "message": body}

    def create_draft(self, raw: bytes, thread_id: str | None) -> CanonicalMessage:
        """A draft whose In-Reply-To (or References) names a message of the mailbox answers it
        (as in `send`): drafts.send then is ONE engine `reply`, not a plain `send`."""
        parsed, content = self._draft_content(raw)
        self._check_thread(thread_id)
        orig = self._reply_target(parsed)
        out = self.mb.create_draft(**content, in_reply_to=parsed.in_reply_to,
                                   references=parsed.references or None,
                                   reply_to=orig.id if orig is not None else None)  # fmt: skip
        self._changed()
        return self.mb.get_message(out["message_id"])

    def update_draft(self, gdid: str, raw: bytes) -> CanonicalMessage:
        """Replace the draft's content; the draft id stays, the message id changes (as in
        Gmail). The new message's In-Reply-To / References decide its threading and which
        message it answers, as in drafts.create."""
        current = self.draft(gdid)
        parsed, content = self._draft_content(raw)
        orig = self._reply_target(parsed)
        threading = DraftThreading(parsed.in_reply_to, tuple(parsed.references),
                                   orig.id if orig is not None else None)  # fmt: skip
        out = self.mb.update_draft(current.id, **content, keep_attachments=False,
                                   threading=threading)  # fmt: skip
        self._changed()
        return self.mb.get_message(out["message_id"])

    def send_draft(self, gdid: str, raw: bytes | None) -> CanonicalMessage:
        current = self.update_draft(gdid, raw) if raw is not None else self.draft(gdid)
        out = self.mb.send_draft(current.id)
        self._changed()
        return self.mb.get_message(out["message_id"])

    def list_drafts(self, q: str | None, max_results: int | None, token: str | None,
                    include_spam_trash: bool) -> dict[str, Any]:  # fmt: skip
        size = page_size(max_results)
        drafts = [x for x in self.select(q, [], include_spam_trash) if x.is_draft]
        page, nxt = paginate(drafts, lambda x: (message_date(x), gmail_draft_id(x)), size, token)
        out: dict[str, Any] = {"resultSizeEstimate": len(drafts)}
        if page:
            out["drafts"] = [{"id": gmail_draft_id(x), "message": stub(x)} for x in page]
        if nxt:
            out["nextPageToken"] = nxt
        return out

    # -------------------------------------------------------------------------- labels

    def _key(self) -> tuple[str, str]:
        return str(self.principal.run_id), str(self.principal.mailbox_id)

    def _registered(self) -> dict[str, dict[str, Any]]:
        """The adapter-side registry for this (run, mailbox); emptied when the run was reset
        after its entries were recorded (labels do not survive a reset)."""
        key = self._key()
        since = self._registry.since(key)
        if since is not None and self.mb.list_changes(since, limit=1)["reset_since"]:
            self._registry.clear(key)
        return self._registry.get(key)

    def _register(self, name: str, settings: dict[str, Any]) -> None:
        self._registry.put(self._key(), name, settings, int(self.history_id()))

    def label_settings(self) -> dict[str, dict[str, Any]]:
        names: dict[str, dict[str, Any]] = {
            n: {} for x in self.all_messages() for n in lb.user_labels(x)
        }
        names.update(self._registered())
        return names

    def list_labels(self) -> dict[str, Any]:
        users = sorted(self.label_settings().items())
        return {"labels": [lb.system_label(x) for x in lb.SYSTEM_LABELS]
                + [lb.user_label(n, s) for n, s in users]}  # fmt: skip

    def _existing(self, label_id: str) -> tuple[str, dict[str, Any]]:
        if lb.check_label_id(label_id) != "user":
            raise InvalidRequest(f"Invalid label: {label_id} is not a user label")
        name = lb.user_label_name(label_id)
        settings = self.label_settings()
        if name not in settings:
            raise NotFound(f"Label {label_id} not found")
        return name, settings[name]

    def get_label(self, label_id: str) -> dict[str, Any]:
        kind = lb.check_label_id(label_id)
        if kind == "unmodelled":
            raise NotFound(f"Label {label_id} not found")
        out = (
            lb.system_label(label_id)
            if kind == "system"
            else lb.user_label(*self._existing(label_id))
        )
        tagged = [x for x in self.all_messages() if label_id in lb.message_label_ids(x)]
        unread = [x for x in tagged if lb.is_unread(x)]
        out.update(messagesTotal=len(tagged), messagesUnread=len(unread),
                   threadsTotal=len({x.thread_id for x in tagged}),
                   threadsUnread=len({x.thread_id for x in unread}))  # fmt: skip
        return out

    def _check_free(self, name: str, ignore: str | None = None) -> None:
        taken = {n.casefold() for n in self.label_settings() if n != ignore}
        if name.casefold() in taken:
            raise Conflict("Label name exists or conflicts")

    def create_label(self, body: dict[str, Any]) -> dict[str, Any]:
        name = lb.check_user_label_name(body.get("name"))
        self._check_free(name)
        settings = lb.check_settings(body)
        self._register(name, settings)
        return lb.user_label(name, settings)

    def update_label(self, label_id: str, body: dict[str, Any], patch: bool) -> dict[str, Any]:
        """Rename and/or change settings. A rename rewrites the label on every message that has
        it (one `label` write per message, not atomic: if a write fails, the messages already
        rewritten keep the new name) and changes the label id (ids derive from names)."""
        name, settings = self._existing(label_id)
        new_name = lb.check_user_label_name(body.get("name", name) if patch else body.get("name"))
        fresh = lb.check_settings(body)
        merged = {**settings, **fresh} if patch else fresh
        if new_name != name:
            self._check_free(new_name, ignore=name)
            for msg in self.all_messages():
                if name in lb.user_labels(msg):
                    self.mb.modify_labels(msg.id, add=[new_name], remove=[name])
            self._registry.drop(self._key(), name)
            self._changed()
        self._register(new_name, merged)
        return lb.user_label(new_name, merged)

    def delete_label(self, label_id: str) -> None:
        """One `label` write per message carrying it (not atomic, like a rename)."""
        name, _ = self._existing(label_id)
        for msg in self.all_messages():
            if name in lb.user_labels(msg):
                self.mb.modify_labels(msg.id, remove=[name])
        self._registry.drop(self._key(), name)
        self._changed()

    # ------------------------------------------------------------------------- history

    def history(self, start: str | None, max_results: int | None, token: str | None,
                label_id: str | None, history_types: list[str]) -> dict[str, Any]:  # fmt: skip
        since = history_number(start, "startHistoryId is required (a non-negative integer)")
        unknown = set(history_types) - HISTORY_TYPES
        if unknown:
            raise InvalidRequest(f"Invalid historyTypes: {sorted(unknown)}")
        if label_id:
            lb.check_label_id(label_id)
        if token:
            since = history_number(decode_cursor(token)[0], "malformed page token")
        feed = self.mb.list_changes(since, limit=page_size(max_results))
        if feed["reset_since"]:
            raise NotFound("Requested entity was not found: startHistoryId predates a run reset; "
                           "perform a full sync")  # fmt: skip
        changes = [c for c in feed["changes"] if c.get("message_id")]
        ids = list(dict.fromkeys(str(c["message_id"]) for c in changes))
        present = self.mb.present_messages(ids)  # deleted ones are absent, not agent errors
        current: dict[str, CanonicalMessage | None] = {mid: present.get(mid) for mid in ids}
        wanted = set(history_types)
        records = []
        for change in changes:
            msg = current[str(change["message_id"])]
            record, kinds = history_record(change, msg)
            if wanted and not kinds & wanted:
                continue
            if label_id and (msg is None or label_id not in lb.message_label_ids(msg)):
                continue
            records.append(record)
        out: dict[str, Any] = {"historyId": str(feed["latest_sequence"])}
        if records:
            out["history"] = records
        if feed["has_more"]:
            out["nextPageToken"] = encode_cursor(str(feed["next_since"]), "h")
        return out
