"""Frozen cross-adapter contract-test drivers (Wave 0).

Every agent-facing interface (neutral facade now; REST, Gmail, Graph, IMAP+SMTP and MCP in
Waves 1-2) implements `MailboxDriver` and is appended to `DRIVERS`. The same tests in
`test_contract.py` then run against each, and read-after-write is checked across every
(writer, reader) pair on the SAME run (spec 5.5: one canonical state behind all adapters).

Drivers translate their native ids/errors into the neutral vocabulary:
- message references are the driver's own ids; `canonical_id()` maps back for comparisons
- errors are normalised with `error_code(exc)` to `mailsim.domain.errors.ErrorCode` values
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from mailsim.domain.errors import ErrorCode, SimError
from mailsim.engine.facades import AgentMailbox, harness_parts

ALL_CAPABILITIES = frozenset(
    {"list", "page", "get", "thread", "send", "reply", "move", "flag", "label", "delete",
     "changes", "search"}
)  # fmt: skip


@dataclass(frozen=True)
class Msg:
    """Neutral view of a message as seen through a driver."""

    ref: str  # driver-native id
    canonical_id: str
    message_id_header: str
    thread_ref: str
    subject: str
    folder: str  # system type: inbox|sent|drafts|trash|archive
    flags: frozenset[str]
    labels: frozenset[str]
    in_reply_to: str | None = None
    body: str = ""  # everything textual the agent can read through this driver (body, headers)


class MailboxDriver(Protocol):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[str]]

    def list(self, folder: str | None = None) -> list[Msg]: ...
    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]: ...
    def get(self, ref: str) -> Msg: ...
    def thread(self, thread_ref: str) -> list[Msg]: ...
    def search(self, text: str) -> list[Msg]: ...
    def send(self, to: list[str], subject: str, text: str) -> str: ...  # returns Message-ID header
    def reply(self, ref: str, text: str) -> None: ...
    def move(self, ref: str, folder: str) -> None: ...
    def flag(self, ref: str, add: list[str]) -> None: ...
    def label(self, ref: str, add: list[str]) -> None: ...
    def delete(self, ref: str) -> None: ...
    def changes(self, since: int) -> tuple[list[str], int]: ...  # (canonical ids, cursor)
    def error_code(self, exc: BaseException) -> str: ...


_FOLDER = {
    "INBOX": "inbox",
    "Sent": "sent",
    "Drafts": "drafts",
    "Trash": "trash",
    "Archive": "archive",
}


class NeutralDriver:
    """The neutral engine facade (what REST and every adapter must agree with)."""

    name: ClassVar[str] = "neutral"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES

    def __init__(self, mailbox: AgentMailbox) -> None:
        self.mb = mailbox

    @staticmethod
    def _msg(m: Any) -> Msg:
        return Msg(m.id, m.id, m.message_id_header, m.thread_id, m.subject, _FOLDER[m.folder],
                   frozenset(m.flags), frozenset(m.labels), m.in_reply_to,
                   m.model_dump_json())  # fmt: skip

    def list(self, folder: str | None = None) -> list[Msg]:
        return [self._msg(m) for m in self.mb.list_messages(folder=folder, page_size=500).items]

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        p = self.mb.list_messages(page_size=size, cursor=cursor)
        return [self._msg(m) for m in p.items], p.next_cursor

    def get(self, ref: str) -> Msg:
        return self._msg(self.mb.get_message(ref))

    def thread(self, thread_ref: str) -> list[Msg]:
        return [self._msg(m) for m in self.mb.get_thread(thread_ref)]

    def search(self, text: str) -> list[Msg]:
        return [self._msg(m) for m in self.mb.search_messages(text)]

    def send(self, to: list[str], subject: str, text: str) -> str:
        return str(self.mb.send(to=to, subject=subject, text=text)["message_id_header"])

    def reply(self, ref: str, text: str) -> None:
        self.mb.reply(ref, text=text)

    def move(self, ref: str, folder: str) -> None:
        self.mb.move(ref, folder)

    def flag(self, ref: str, add: list[str]) -> None:
        self.mb.set_flags(ref, add=add)

    def label(self, ref: str, add: list[str]) -> None:
        self.mb.modify_labels(ref, add=add)

    def delete(self, ref: str) -> None:
        self.mb.delete(ref)

    def changes(self, since: int) -> tuple[list[str], int]:
        ids: list[str] = []
        while True:
            feed = self.mb.list_changes(since)
            ids += [c["message_id"] for c in feed["changes"]]
            since = int(feed["next_since"])
            if not feed["has_more"]:
                return ids, since

    def error_code(self, exc: BaseException) -> str:
        return str(exc.code) if isinstance(exc, SimError) else type(exc).__name__


# Waves 1-2 append: RestDriver, GmailDriver, GraphDriver, ImapSmtpDriver, McpDriver.
DRIVERS: list[type[NeutralDriver]] = [NeutralDriver]

__all__ = ["ALL_CAPABILITIES", "DRIVERS", "ErrorCode", "MailboxDriver", "Msg", "NeutralDriver"]


# ---- Wave 1 (WS-A): the neutral REST API, driven over HTTP with an agent key ----------------
class RestError(Exception):
    """A non-2xx REST response; `code` comes from the frozen error envelope."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        self.code = str(body.get("error", {}).get("code", f"HTTP_{status}"))
        super().__init__(f"{status} {self.code}: {body}")


class RestDriver(NeutralDriver):
    """`/v1/mailboxes/{id}/...` over HTTP (FastAPI TestClient), authenticated with an agent
    key bound to this run and to `mailbox.mailbox_id`. For a mailbox that is not the
    scenario's agent mailbox, the engine's agent guard then answers PERMISSION_DENIED, which is
    what the isolation contract expects."""

    name: ClassVar[str] = "rest"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES

    def __init__(self, mailbox: AgentMailbox) -> None:
        from fastapi.testclient import TestClient

        from mailsim.api.app import create_app
        from mailsim.api.auth import issue_key, verify_key

        # the harness shares the engine/scope with the app so both drivers act on one state
        engine, scope = harness_parts(mailbox)
        sf = engine.session_factory
        with sf.begin() as s:
            key = issue_key(s, tenant_id=scope.tenant_id, project_id=scope.project_id,
                            role="agent", run_id=mailbox.run_id,
                            mailbox_id=mailbox.mailbox_id).api_key  # fmt: skip
            principal = verify_key(s, key)
        assert (principal.run_id, principal.mailbox_id) == (mailbox.run_id, mailbox.mailbox_id)
        app = create_app(engine=engine, session_factory=sf, blobs=engine.blobs)
        self.http = TestClient(app, raise_server_exceptions=False)
        self.http.headers["Authorization"] = f"Bearer {key}"
        self.base = f"/v1/mailboxes/{mailbox.mailbox_id}"

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, self.base + path, **kw)
        if r.status_code >= 400:
            raise RestError(r.status_code, r.json())
        return r.json() if r.content else None

    @staticmethod
    def _rmsg(d: dict[str, Any]) -> Msg:
        import json

        return Msg(d["id"], d["id"], d["message_id_header"], d["thread_id"], d["subject"],
                   _FOLDER[d["folder"]], frozenset(d["flags"]), frozenset(d["labels"]),
                   d["in_reply_to"], json.dumps(d))  # fmt: skip

    def _all(self, **params: Any) -> list[Msg]:
        out, cursor = [], None
        while True:
            page = self._call("GET", "/messages", params={**params, "page_size": 500,
                                                           **({"cursor": cursor} if cursor else {})})  # fmt: skip
            out += [self._rmsg(d) for d in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                return out

    def list(self, folder: str | None = None) -> list[Msg]:
        return self._all(**({"folder": folder} if folder else {}))

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        params: dict[str, Any] = {"page_size": size, **({"cursor": cursor} if cursor else {})}
        p = self._call("GET", "/messages", params=params)
        return [self._rmsg(d) for d in p["items"]], p["next_cursor"]

    def get(self, ref: str) -> Msg:
        return self._rmsg(self._call("GET", f"/messages/{ref}"))

    def thread(self, thread_ref: str) -> list[Msg]:
        return [self._rmsg(d) for d in self._call("GET", f"/threads/{thread_ref}")]

    def search(self, text: str) -> list[Msg]:
        return self._all(q=text)

    def send(self, to: list[str], subject: str, text: str) -> str:
        out = self._call("POST", "/messages", json={"to": to, "subject": subject, "text": text})
        return str(out["message_id_header"])

    def reply(self, ref: str, text: str) -> None:
        self._call("POST", f"/messages/{ref}/reply", json={"text": text})

    def move(self, ref: str, folder: str) -> None:
        self._call("PATCH", f"/messages/{ref}", json={"folder": folder})

    def flag(self, ref: str, add: list[str]) -> None:
        self._call("PATCH", f"/messages/{ref}", json={"add_flags": add})

    def label(self, ref: str, add: list[str]) -> None:
        self._call("PATCH", f"/messages/{ref}", json={"add_labels": add})

    def delete(self, ref: str) -> None:
        self._call("DELETE", f"/messages/{ref}")

    def changes(self, since: int) -> tuple[list[str], int]:
        ids: list[str] = []
        while True:
            feed = self._call("GET", "/changes", params={"since": since})
            ids += [c["message_id"] for c in feed["changes"]]
            since = int(feed["next_since"])
            if not feed["has_more"]:
                return ids, since

    def error_code(self, exc: BaseException) -> str:
        return exc.code if isinstance(exc, RestError) else super().error_code(exc)


DRIVERS.append(RestDriver)


# ---- Wave 2 (WS-C): the Gmail API v1 compatibility profile, over HTTP with an agent key -----
class GmailApiError(Exception):
    """A non-2xx Gmail-shaped response; `code` is the neutral code from the ErrorInfo detail."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        details = body.get("error", {}).get("details") or [{}]
        self.code = str(details[0].get("reason") or f"HTTP_{status}")
        super().__init__(f"{status} {self.code}: {body}")


class GmailDriver(NeutralDriver):
    """`/gmail/v1/users/me/...` over HTTP (FastAPI TestClient) with an agent key, using only
    Gmail-native requests: folders and flags are label changes, replies are raw RFC 822 sends
    with In-Reply-To, user labels are created through labels.create. The bound AgentMailbox is
    used only to translate Gmail ids back to canonical ids for cross-driver comparison."""

    name: ClassVar[str] = "gmail"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES
    _FOLDER_LABEL: ClassVar[dict[str, str]] = {"inbox": "INBOX", "sent": "SENT", "drafts": "DRAFT",
                                                "trash": "TRASH"}  # fmt: skip
    _FLAG_ADD: ClassVar[dict[str, str]] = {"FLAGGED": "STARRED", "ANSWERED": "MAILSIM_ANSWERED",
                                            "DELETED": "MAILSIM_DELETED", "DRAFT": "DRAFT"}  # fmt: skip

    def __init__(self, mailbox: AgentMailbox) -> None:
        from fastapi.testclient import TestClient

        from mailsim.api.app import create_app
        from mailsim.api.auth import issue_key
        from mailsim.serve import mount_adapters

        engine, scope = harness_parts(mailbox)
        sf = engine.session_factory
        with sf.begin() as s:
            key = issue_key(s, tenant_id=scope.tenant_id, project_id=scope.project_id,
                            role="agent", run_id=mailbox.run_id,
                            mailbox_id=mailbox.mailbox_id).api_key  # fmt: skip
        app = create_app(engine=engine, session_factory=sf, blobs=engine.blobs)
        assert mount_adapters(app, ["mailsim.adapters.gmail.api"])
        self.mb = mailbox
        self.http = TestClient(app, raise_server_exceptions=False)
        self.http.headers["Authorization"] = f"Bearer {key}"
        self.base = "/gmail/v1/users/me"

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, self.base + path, **kw)
        if r.status_code >= 400:
            raise GmailApiError(r.status_code, r.json())
        return r.json() if r.content else None

    @staticmethod
    def _text(part: dict[str, Any]) -> str:
        import base64

        data = part.get("body", {}).get("data")
        own = base64.urlsafe_b64decode(data).decode("utf-8", "replace") if data else ""
        return own + "".join(GmailDriver._text(p) for p in part.get("parts", []))

    def _msg(self, res: dict[str, Any]) -> Msg:
        import json

        headers = {h["name"].lower(): h["value"] for h in res["payload"]["headers"]}
        labels = set(res["labelIds"])
        folder = next((f for f, lab in self._FOLDER_LABEL.items() if lab in labels), "archive")
        flags = {f for f, lab in self._FLAG_ADD.items() if lab in labels}
        if "UNREAD" not in labels:
            flags.add("SEEN")
        from mailsim.adapters.gmail.labels import is_user_label_id, user_label_name

        names = {user_label_name(x) for x in labels if is_user_label_id(x)}
        return Msg(res["id"], self.mb.resolve_external_id("gmail", "message", res["id"]),
                   headers["message-id"], res["threadId"], headers.get("subject", ""), folder,
                   frozenset(flags), frozenset(names), headers.get("in-reply-to"),
                   json.dumps(res) + self._text(res["payload"]))  # fmt: skip

    def _ids(self, **params: Any) -> list[str]:
        out, token = [], None
        while True:
            page = self._call("GET", "/messages", params={**params, "maxResults": 500,
                                                           "includeSpamTrash": "true",
                                                           **({"pageToken": token} if token else {})})  # fmt: skip
            out += [m["id"] for m in page.get("messages", [])]
            token = page.get("nextPageToken")
            if token is None:
                return out

    def list(self, folder: str | None = None) -> list[Msg]:
        if folder == "archive":
            ids = self._ids(q="in:archive")
        else:
            ids = self._ids(**({"labelIds": self._FOLDER_LABEL[folder]} if folder else {}))
        return [
            self.get(i) for i in reversed(ids)
        ]  # Gmail lists newest first; neutral is oldest first

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        params: dict[str, Any] = {"maxResults": size, "includeSpamTrash": "true",
                                  **({"pageToken": cursor} if cursor else {})}  # fmt: skip
        p = self._call("GET", "/messages", params=params)
        return [self.get(m["id"]) for m in p.get("messages", [])], p.get("nextPageToken")

    def get(self, ref: str) -> Msg:
        return self._msg(self._call("GET", f"/messages/{ref}", params={"format": "full"}))

    def thread(self, thread_ref: str) -> list[Msg]:
        t = self._call("GET", f"/threads/{thread_ref}", params={"format": "full"})
        return [self._msg(m) for m in t["messages"]]

    def search(self, text: str) -> list[Msg]:
        return [self.get(i) for i in self._ids(q=text)]

    @staticmethod
    def _raw(headers: dict[str, str], text: str) -> str:
        import base64
        from email.message import EmailMessage

        em = EmailMessage()
        for k, v in headers.items():
            em[k] = v
        em.set_content(text)
        return base64.urlsafe_b64encode(em.as_bytes()).decode()

    def send(self, to: list[str], subject: str, text: str) -> str:
        sent = self._call("POST", "/messages/send",
                          json={"raw": self._raw({"To": ", ".join(to), "Subject": subject}, text)})  # fmt: skip
        return self.get(sent["id"]).message_id_header

    def reply(self, ref: str, text: str) -> None:
        orig = self._call("GET", f"/messages/{ref}", params={"format": "metadata"})
        h = {x["name"].lower(): x["value"] for x in orig["payload"]["headers"]}
        subject = h.get("subject", "")
        refs = " ".join(filter(None, [h.get("references"), h["message-id"]]))
        headers = {"To": h["from"], "In-Reply-To": h["message-id"], "References": refs,
                   "Subject": subject if subject.lower().startswith("re:") else f"Re: {subject}"}  # fmt: skip
        self._call("POST", "/messages/send",
                   json={"raw": self._raw(headers, text), "threadId": orig["threadId"]})  # fmt: skip

    def _modify(self, ref: str, add: list[str] = (), remove: list[str] = ()) -> None:  # type: ignore[assignment]
        self._call("POST", f"/messages/{ref}/modify",
                   json={"addLabelIds": list(add), "removeLabelIds": list(remove)})  # fmt: skip

    def move(self, ref: str, folder: str) -> None:
        if folder == "trash":
            self._call("POST", f"/messages/{ref}/trash")
        elif folder == "archive":
            labels = self._call("GET", f"/messages/{ref}", params={"format": "minimal"})["labelIds"]
            self._modify(ref, remove=[x for x in labels if x in ("INBOX", "SENT")])
        else:
            self._modify(ref, add=[self._FOLDER_LABEL.get(folder, folder)])

    def flag(self, ref: str, add: list[str]) -> None:
        self._modify(ref, add=[self._FLAG_ADD[f] for f in add if f != "SEEN"],
                     remove=["UNREAD"] if "SEEN" in add else [])  # fmt: skip

    def label(self, ref: str, add: list[str]) -> None:
        existing = {x["name"]: x["id"] for x in self._call("GET", "/labels")["labels"]}
        ids = []
        for name in add:
            if name not in existing:
                try:
                    existing[name] = self._call("POST", "/labels", json={"name": name})["id"]
                except GmailApiError as exc:  # created concurrently: it exists now
                    assert exc.code == ErrorCode.CONFLICT
                    existing = {x["name"]: x["id"] for x in self._call("GET", "/labels")["labels"]}
            ids.append(existing[name])
        self._modify(ref, add=ids)

    def delete(self, ref: str) -> None:
        self._call("DELETE", f"/messages/{ref}")

    def changes(self, since: int) -> tuple[list[str], int]:
        ids: list[str] = []
        token = None
        while True:
            params: dict[str, Any] = {"startHistoryId": since,
                                      **({"pageToken": token} if token else {})}  # fmt: skip
            feed = self._call("GET", "/history", params=params)
            for record in feed.get("history", []):
                gid = record["messages"][0]["id"]
                try:
                    ids.append(self.mb.resolve_external_id("gmail", "message", gid))
                except SimError:  # deleted since: no canonical id left to resolve
                    ids.append(gid)
            token = feed.get("nextPageToken")
            if token is None:
                return ids, int(feed["historyId"])

    def error_code(self, exc: BaseException) -> str:
        return exc.code if isinstance(exc, GmailApiError) else super().error_code(exc)


DRIVERS.append(GmailDriver)


# ---- Wave 2 (WS-D): the Microsoft Graph v1.0 mail profile, driven over HTTP ---------------
_GRAPH_FOLDERS = {"inbox": "inbox", "sent": "sentitems", "drafts": "drafts",
                  "trash": "deleteditems", "archive": "archive"}  # fmt: skip
_GRAPH_CODES = {
    "ErrorInvalidRequest": ErrorCode.INVALID_REQUEST, "ErrorItemNotFound": ErrorCode.NOT_FOUND,
    "ErrorAccessDenied": ErrorCode.PERMISSION_DENIED,
    "ErrorNotSupported": ErrorCode.UNSUPPORTED_OPERATION,
    "ErrorIrresolvableConflict": ErrorCode.CONFLICT,
    "ErrorTooManyRequests": ErrorCode.RATE_LIMITED,
    "ErrorQuotaExceeded": ErrorCode.SIMULATION_LIMIT_EXCEEDED,
    "ErrorPreconditionFailed": ErrorCode.RUN_NOT_ACTIVE,
    "ErrorMessageSubmissionBlocked": ErrorCode.EXTERNAL_DELIVERY_BLOCKED,
    "ErrorServiceUnavailable": ErrorCode.UPSTREAM_UNAVAILABLE,
}  # fmt: skip
_GRAPH_SELECT = ("id,subject,body,internetMessageId,conversationId,parentFolderId,isRead,"
                 "isDraft,flag,categories,internetMessageHeaders,from,toRecipients")  # fmt: skip
_GRAPH_EXPAND = "singleValueExtendedProperties($filter=id eq 'Integer 0x1081')"


class GraphDriver(RestDriver):
    """`/graph/v1.0/me/...` over HTTP with an agent key, speaking only Graph: Graph ids,
    well-known folder names, isRead/flag/categories, the PidTagLastVerbExecuted extended
    property for ANSWERED, sendMail, permanentDelete and messages/delta. Canonical ids are
    looked up through the agent facade (test harness only) for cross-driver comparisons."""

    name: ClassVar[str] = "graph"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES

    def __init__(self, mailbox: AgentMailbox) -> None:
        super().__init__(mailbox)
        from mailsim.adapters.graph.api import router

        self.http.app.include_router(router)  # type: ignore[attr-defined]
        self.mb = mailbox
        self.base = "/graph/v1.0/me"
        self._folders: dict[str, str] | None = None

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path if path.startswith("http") else self.base + path, **kw)
        if r.status_code >= 400:
            raise RestError(r.status_code, r.json())
        return r.json() if r.content else None

    def _folder_types(self) -> dict[str, str]:
        if self._folders is None:
            self._folders = {self._call("GET", f"/mailFolders/{wk}")["id"]: st
                             for st, wk in _GRAPH_FOLDERS.items()}  # fmt: skip
        return self._folders

    def _canonical(self, ref: str) -> str:
        for kind in ("message", "draft"):
            try:
                return self.mb.resolve_external_id("graph", kind, ref)
            except SimError:
                continue
        return ref

    def _gmsg(self, d: dict[str, Any]) -> Msg:
        import json

        headers = {h["name"].lower(): h["value"] for h in d.get("internetMessageHeaders", [])}
        flags = {"SEEN"} if d["isRead"] else set()
        flags |= {"FLAGGED"} if d["flag"]["flagStatus"] == "flagged" else set()
        flags |= {"DRAFT"} if d["isDraft"] else set()
        flags |= {"ANSWERED"} if d.get("singleValueExtendedProperties") else set()
        return Msg(d["id"], self._canonical(d["id"]), d["internetMessageId"], d["conversationId"],
                   d["subject"], self._folder_types()[d["parentFolderId"]], frozenset(flags),
                   frozenset(d["categories"]), headers.get("in-reply-to"), json.dumps(d))  # fmt: skip

    def _params(self, **extra: Any) -> dict[str, Any]:
        return {"$select": _GRAPH_SELECT, "$expand": _GRAPH_EXPAND, **extra}

    def _collect(self, path: str, **params: Any) -> list[Msg]:
        out: list[Msg] = []
        page = self._call("GET", path, params=self._params(**{"$top": 1000, **params}))
        while True:
            out += [self._gmsg(d) for d in page["value"]]
            if "@odata.nextLink" not in page:
                return out
            page = self._call("GET", page["@odata.nextLink"])

    def list(self, folder: str | None = None) -> list[Msg]:
        path = f"/mailFolders/{_GRAPH_FOLDERS[folder]}/messages" if folder else "/messages"
        return self._collect(path, **{"$orderby": "receivedDateTime asc"})  # neutral's order

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        p = (self._call("GET", cursor) if cursor
             else self._call("GET", "/messages", params=self._params(**{"$top": size})))  # fmt: skip
        return [self._gmsg(d) for d in p["value"]], p.get("@odata.nextLink")

    def get(self, ref: str) -> Msg:
        return self._gmsg(self._call("GET", f"/messages/{ref}", params=self._params()))

    def thread(self, thread_ref: str) -> list[Msg]:
        return self._collect("/messages", **{"$filter": f"conversationId eq '{thread_ref}'"})

    def search(self, text: str) -> list[Msg]:
        return self._collect("/messages", **{"$search": f'"{text}"'})

    def send(self, to: list[str], subject: str, text: str) -> str:
        before = {m.ref for m in self.list("sent")}
        message = {"subject": subject, "body": {"contentType": "text", "content": text},
                   "toRecipients": [{"emailAddress": {"address": a}} for a in to]}  # fmt: skip
        self._call("POST", "/sendMail", json={"message": message})
        (new,) = [m for m in self.list("sent") if m.ref not in before]
        return new.message_id_header

    def reply(self, ref: str, text: str) -> None:
        self._call("POST", f"/messages/{ref}/reply", json={"comment": text})

    def move(self, ref: str, folder: str) -> None:
        dest = _GRAPH_FOLDERS.get(folder, folder)
        self._call("POST", f"/messages/{ref}/move", json={"destinationId": dest})

    def flag(self, ref: str, add: list[str]) -> None:
        patch: dict[str, Any] = {}
        if "SEEN" in add:
            patch["isRead"] = True
        if "FLAGGED" in add:
            patch["flag"] = {"flagStatus": "flagged"}
        if "ANSWERED" in add:
            patch["singleValueExtendedProperties"] = [{"id": "Integer 0x1081", "value": "102"}]
        self._call("PATCH", f"/messages/{ref}", json=patch)

    def label(self, ref: str, add: list[str]) -> None:
        current = self._call("GET", f"/messages/{ref}", params={"$select": "categories"})
        merged = list(dict.fromkeys([*current["categories"], *add]))
        self._call("PATCH", f"/messages/{ref}", json={"categories": merged})

    def delete(self, ref: str) -> None:
        self._call("POST", f"/messages/{ref}/permanentDelete")

    def changes(self, since: Any) -> tuple[list[str], Any]:
        """`since` is 0 (initial sync) or the deltaLink of the previous round."""
        url = "/messages/delta" if since == 0 else since
        ids: list[str] = []
        page = self._call("GET", url, params={"$select": "id"} if since == 0 else None)
        while True:
            for d in page["value"]:
                if "@removed" not in d:
                    ids.append(self._canonical(d["id"]))
            if "@odata.deltaLink" in page:
                return ids, page["@odata.deltaLink"]
            page = self._call("GET", page["@odata.nextLink"])

    def error_code(self, exc: BaseException) -> str:
        if isinstance(exc, RestError):
            return str(_GRAPH_CODES.get(exc.code, exc.code))
        return super().error_code(exc)


DRIVERS.append(GraphDriver)


# ---- Wave 2 (WS-F): the MCP agent tools, driven by the official MCP client ------------------
class McpToolError(Exception):
    """An MCP tool result with isError; `code` comes from its structured error."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        self.code = str(error.get("code"))
        super().__init__(f"{self.code}: {error.get('message')}")


class McpDriver(NeutralDriver):
    """The MCP agent tools over streamable HTTP (`/mcp`, ASGI in-process), through the official
    MCP client and an agent key bound to this run and `mailbox.mailbox_id`. Every operation
    is one MCP connection + one `tools/call`, authenticated per request."""

    name: ClassVar[str] = "mcp"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES

    def __init__(self, mailbox: AgentMailbox) -> None:
        from mailsim.api.app import create_app
        from mailsim.api.auth import issue_key
        from mailsim.mcp.http import router

        engine, scope = harness_parts(mailbox)
        with engine.session_factory.begin() as s:
            self.key = issue_key(s, tenant_id=scope.tenant_id, project_id=scope.project_id,
                                 role="agent", run_id=mailbox.run_id,
                                 mailbox_id=mailbox.mailbox_id).api_key  # fmt: skip
        self.app = create_app(engine=engine, session_factory=engine.session_factory,
                              blobs=engine.blobs)  # fmt: skip
        self.app.include_router(router)

    def _call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        import anyio
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        from mcp import Client

        async def go() -> Any:
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app),
                                      base_url="http://127.0.0.1",
                                      headers={"Authorization": f"Bearer {self.key}"})  # fmt: skip
            async with http, Client(streamable_http_client("http://127.0.0.1/mcp",
                                                           http_client=http)) as client:  # fmt: skip
                return await client.call_tool(tool, args or {})

        result = anyio.run(go)
        if result.is_error:
            raise McpToolError(result.structured_content["error"])
        return result.structured_content

    @staticmethod
    def _mmsg(d: dict[str, Any]) -> Msg:
        return RestDriver._rmsg(d)

    def _all(self, tool: str, **args: Any) -> list[Msg]:
        out, cursor = [], None
        while True:
            page = self._call(
                tool, {**args, "page_size": 500, **({"cursor": cursor} if cursor else {})}
            )
            out += [self._mmsg(d) for d in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                return out

    def list(self, folder: str | None = None) -> list[Msg]:
        return self._all("list_messages", **({"folder": folder} if folder else {}))

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        p = self._call(
            "list_messages", {"page_size": size, **({"cursor": cursor} if cursor else {})}
        )
        return [self._mmsg(d) for d in p["items"]], p["next_cursor"]

    def get(self, ref: str) -> Msg:
        return self._mmsg(self._call("get_message", {"message_id": ref}))

    def thread(self, thread_ref: str) -> list[Msg]:
        return [self._mmsg(d) for d in self._call("get_thread", {"thread_id": thread_ref})["items"]]

    def search(self, text: str) -> list[Msg]:
        return self._all("search_messages", query=text)

    def send(self, to: list[str], subject: str, text: str) -> str:
        out = self._call("send_message", {"to": to, "subject": subject, "text": text})
        return str(out["message_id_header"])

    def reply(self, ref: str, text: str) -> None:
        self._call("reply_to_message", {"message_id": ref, "text": text})

    def move(self, ref: str, folder: str) -> None:
        self._call("manage_labels", {"message_id": ref, "folder": folder})

    def flag(self, ref: str, add: list[str]) -> None:
        self._call("manage_labels", {"message_id": ref, "add_flags": add})

    def label(self, ref: str, add: list[str]) -> None:
        self._call("manage_labels", {"message_id": ref, "add_labels": add})

    def delete(self, ref: str) -> None:
        self._call("delete_message", {"message_id": ref})

    def changes(self, since: int) -> tuple[list[str], int]:
        ids: list[str] = []
        while True:
            feed = self._call("get_changes", {"since": since})
            ids += [c["message_id"] for c in feed["changes"]]
            since = int(feed["next_since"])
            if not feed["has_more"]:
                return ids, since

    def error_code(self, exc: BaseException) -> str:
        return exc.code if isinstance(exc, McpToolError) else super().error_code(exc)


DRIVERS.append(McpDriver)
