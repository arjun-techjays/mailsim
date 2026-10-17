"""Microsoft Graph v1.0 mail compatibility profile (spec 5.2). Owner: WS-D.

Mounted by `mailsim.serve.mount_adapters` under `/graph/v1.0`. Every path exists twice:
`/me/...` and `/users/{id}/...`, where `{id}` must name the credential's own mailbox (its
address, mailbox id or Graph user id). Authentication is ALWAYS a sandbox agent key
(`Authorization: Bearer msk_...`); no local/no-auth mode is honoured here.

The engine is reached only through the `AgentMailbox` facade of the authenticated agent, so
the agent guards (allowed operations, run state, mailbox binding, write budget) apply to every
call. Supported operations, field mappings, the OData subset and deviations are documented
in docs/compat/graph.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cmp_to_key
from html import escape
from typing import Annotated, Any, NoReturn, TypeVar
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from mailsim.adapters.graph import kql, odata
from mailsim.adapters.graph import resources as res
from mailsim.adapters.graph.errors import GraphAuthError, GraphRoute, SyncStateGone
from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.api.downloads import safe_download_headers
from mailsim.api.routers._common import app_state
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import (
    InvalidRequest,
    NotFound,
    PermissionDenied,
    UnsupportedOperation,
)
from mailsim.domain.models import SYSTEM_FOLDERS, CanonicalMessage
from mailsim.engine.facades import AgentMailbox, facade_for
from mailsim.providers.ids import graph_id

PREFIX = "/graph/v1.0"
BASES = ("/me", "/users/{user_id}")
ENGINE_PAGE = 500
DELTA_DEFAULT_PAGE = 10
MAX_TOP_WITH_ATTACHMENTS = 50
# $expand=attachments on a list: a page stops before the attachment bytes it carries would
# exceed this (always at least one message; the rest via @odata.nextLink)
MAX_EXPANDED_ATTACHMENT_BYTES = 25 * 1024 * 1024
ITEM_NOT_FOUND = "The specified object was not found in the store."
LIST_OPTIONS = frozenset(
    {"$top", "$skip", "$skiptoken", "$select", "$filter", "$orderby", "$count", "$search",
     "$expand"}
)  # fmt: skip
GET_OPTIONS = frozenset({"$select", "$expand"})
COUNT_OPTIONS = frozenset({"$filter", "$search"})
DELTA_OPTIONS = frozenset({"$select", "$deltatoken", "$skiptoken", "$top"})
_PREFER_BODY = re.compile(r'outlook\.body-content-type\s*=\s*"?(text|html)"?', re.IGNORECASE)
_PREFER_PAGE = re.compile(r"odata\.maxpagesize\s*=\s*([0-9]+)", re.IGNORECASE)
CONTENT_FIELDS = ("subject", "body", "toRecipients", "ccRecipients", "bccRecipients")
REJECT_IF_SET = ("attachments", "replyTo", "internetMessageHeaders",
                 "multiValueExtendedProperties", "mentions")  # fmt: skip

router = APIRouter(prefix=PREFIX, tags=["graph"], route_class=GraphRoute)
F = TypeVar("F", bound=Callable[..., Any])


# ----------------------------------------------------------------------- auth / context


def authenticate(request: Request) -> Principal:
    """Sandbox agent key only. Any failure is the same 401 (never says whether a key exists)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise GraphAuthError("Access token is empty or invalid.")
    sf = app_state(request, "session_factory")
    with sf() as session:
        try:
            principal = verify_key(session, token.strip())
        except InvalidCredential:
            raise GraphAuthError("Access token is empty or invalid.") from None
    if principal.role != Role.AGENT:
        raise PermissionDenied("Graph mail endpoints accept agent credentials only")
    request.state.principal = principal
    return principal


@dataclass
class Ctx:
    request: Request
    principal: Principal
    mb: AgentMailbox
    _email: str | None = field(default=None, repr=False)

    @property
    def mailbox_id(self) -> str:
        return self.mb.mailbox_id

    def email(self) -> str:
        """The credential's own mailbox address (from the facade; cached per request)."""
        if self._email is None:
            self._email = str(self.mb.mailbox_info()["email"])
        return self._email

    def is_me(self, ref: str) -> bool:
        if ref in (self.mailbox_id, res.user_id(self.mailbox_id)):
            return True
        return ref.casefold() == self.email().casefold()


def graph_ctx(request: Request) -> Ctx:
    principal = authenticate(request)
    facade = facade_for(app_state(request, "engine"), principal)
    assert isinstance(facade, AgentMailbox)  # agent role -> AgentMailbox (facade_for)
    ctx = Ctx(request, principal, facade)
    user = request.path_params.get("user_id")
    if user is not None and not ctx.is_me(user):
        raise PermissionDenied("Access is denied. Check credentials and try again.")
    return ctx


Context = Annotated[Ctx, Depends(graph_ctx)]
Payload = Annotated[dict[str, Any] | None, Body()]


def route(method: str, path: str, status: int = 200) -> Callable[[F], F]:
    """Register a handler under both `/me` and `/users/{user_id}`."""

    def register(fn: F) -> F:
        for base in BASES:
            router.add_api_route(base + path, fn, methods=[method], status_code=status,
                                 name=f"graph_{fn.__name__}{'_users' if '{' in base else ''}")  # fmt: skip
        return fn

    return register


# ------------------------------------------------------------------------------ helpers


def ok(body: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=dict(headers or {}))


def accepted() -> Response:
    return Response(status_code=202)


def context_url(request: Request, fragment: str) -> str:
    return f"{request.base_url}graph/v1.0/$metadata#{fragment}"


def link(request: Request, params: Mapping[str, str], drop: tuple[str, ...]) -> str:
    keep = [
        (k, v) for k, v in request.query_params.multi_items() if k not in drop and k not in params
    ]
    query = urlencode([*keep, *params.items()], safe="$'(),:/", quote_via=quote)
    return str(request.url.replace(query=query))


def check_options(request: Request, allowed: frozenset[str]) -> None:
    for name in request.query_params:
        if name.startswith("$") and name not in allowed:
            raise odata.QueryNotSupported(f"the query option {name!r} is not supported here")


@dataclass(frozen=True)
class Prefer:
    body: str | None = None
    page_size: int | None = None

    @classmethod
    def parse(cls, request: Request) -> Prefer:
        raw = ",".join(request.headers.getlist("prefer"))
        body, page = _PREFER_BODY.search(raw), _PREFER_PAGE.search(raw)
        size = None
        if page is not None:
            size = odata.parse_int("odata.maxpagesize", page.group(1), default=0, low=1,
                                   high=odata.MAX_TOP)  # fmt: skip
        return cls(body.group(1).lower() if body else None, size)

    def applied(self) -> dict[str, str]:
        parts = []
        if self.body:
            parts.append(f'outlook.body-content-type="{self.body}"')
        if self.page_size:
            parts.append(f"odata.maxpagesize={self.page_size}")
        return {"Preference-Applied": ", ".join(parts)} if parts else {}


def all_messages(mb: AgentMailbox, folder: str | None) -> list[CanonicalMessage]:
    out: list[CanonicalMessage] = []
    cursor: str | None = None
    while True:
        page = mb.list_messages(folder=folder, page_size=ENGINE_PAGE, cursor=cursor)
        out += page.items
        cursor = page.next_cursor
        if cursor is None:
            return out


def resolve_message(ctx: Ctx, message_ref: str) -> str:
    """Graph message (or draft) id -> internal id, only inside this mailbox. One engine read
    tries both kinds, so a valid draft id never leaves a recorded NOT_FOUND behind."""
    try:
        return ctx.mb.resolve_external_id("graph", ("message", "draft"), message_ref)
    except NotFound:
        raise NotFound(ITEM_NOT_FOUND, details={"id": message_ref}) from None


@dataclass(frozen=True)
class FolderRef:
    engine_ref: str  # what the engine accepts: system type or internal folder id
    key: str  # folder_key: system type or name:<name>


def resolve_folder(ctx: Ctx, ref: str) -> FolderRef:
    st = res.WELL_KNOWN.get(ref.lower())
    if st is not None:
        return FolderRef(st, st)
    for _, system_type in SYSTEM_FOLDERS:
        if res.folder_id(ctx.mailbox_id, system_type) == ref:
            return FolderRef(system_type, system_type)
    for f in ctx.mb.list_folders():  # non-system folders (none in today's engine)
        key = res.folder_key(f["name"], f["system_type"])
        if res.folder_id(ctx.mailbox_id, key) == ref:  # pragma: no cover - system folders only
            return FolderRef(f["id"], key)
    raise NotFound("The specified folder could not be found in the store.", details={"id": ref})


def folder_record(ctx: Ctx, ref: FolderRef) -> dict[str, Any]:
    for f in ctx.mb.list_folders():
        if res.folder_key(f["name"], f["system_type"]) == ref.key:
            return f
    raise NotFound("The specified folder could not be found in the store.")


def attachment_docs(mb: AgentMailbox, m: CanonicalMessage) -> list[dict[str, Any]]:
    """Attachment resources with contentBytes: each attachment's bytes are one audited facade
    read (`get_attachment`), like a REST or MCP attachment download."""
    docs = []
    for a in m.attachments:
        ref, data = mb.get_attachment(m.id, a.id)
        docs.append(res.attachment(ref, m, data))
    return docs


def render(request: Request, m: CanonicalMessage, *, select: frozenset[str] | None = None,
           prefer: Prefer | None = None, expand: odata.Expand | None = None,
           mb: AgentMailbox | None = None) -> dict[str, Any]:  # fmt: skip
    atts = (
        attachment_docs(mb, m) if mb is not None and expand is not None and expand.attachments
        else None
    )  # fmt: skip
    return res.message(m, select=select, prefer_body=prefer.body if prefer else None,
                       expand=expand, attachments=atts)  # fmt: skip


def read_options(request: Request) -> tuple[frozenset[str] | None, odata.Expand | None]:
    q = request.query_params
    select = odata.parse_select(q["$select"]) if "$select" in q else None
    expand = odata.parse_expand(q["$expand"]) if "$expand" in q else None
    return select, expand


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, str):
        return (1, value.casefold())
    return (1, value)


def ordered(docs: list[tuple[CanonicalMessage, dict[str, Any]]],
            order: list[tuple[str, bool]]) -> list[tuple[CanonicalMessage, dict[str, Any]]]:  # fmt: skip
    def cmp(a: tuple[CanonicalMessage, dict[str, Any]],
            b: tuple[CanonicalMessage, dict[str, Any]]) -> int:  # fmt: skip
        for prop, desc in order:
            ka, kb = _sort_key(odata.path_get(a[1], prop)), _sort_key(odata.path_get(b[1], prop))
            if ka != kb:
                return (1 if ka > kb else -1) * (-1 if desc else 1)
        ia, ib = (a[0].created_at, a[0].id), (b[0].created_at, b[0].id)
        return 0 if ia == ib else (1 if ia > ib else -1)

    return sorted(docs, key=cmp_to_key(cmp))


# ------------------------------------------------------------------------- write payloads


def _addresses(payload: Mapping[str, Any], name: str) -> list[str] | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidRequest(f"{name} must be a collection of recipients")
    out = []
    for item in value:
        address = odata.path_get(item, "emailAddress/address")
        if not isinstance(address, str) or not address.strip():
            raise InvalidRequest(f"each of {name} needs emailAddress.address")
        out.append(address.strip())
    return out


def _body(payload: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """(text, html) from a Graph itemBody; None when absent."""
    value = payload.get("body")
    if value is None:
        return None
    if not isinstance(value, Mapping) or not isinstance(value.get("content", ""), str):
        raise InvalidRequest("body must be an itemBody {contentType, content}")
    content = str(value.get("content") or "")
    kind = str(value.get("contentType") or "text").lower()
    if kind not in ("text", "html"):
        raise InvalidRequest("body.contentType must be text or html")
    return (res.html_to_text(content), content) if kind == "html" else (content, None)


def _subject(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("subject")
    if value is not None and not isinstance(value, str):
        raise InvalidRequest("subject must be a string")
    return value


def check_writable(ctx: Ctx, payload: Mapping[str, Any]) -> None:
    unsupported = [k for k in REJECT_IF_SET if payload.get(k)]
    if payload.get("importance") not in (None, "normal"):
        unsupported.append("importance")
    if unsupported:
        raise UnsupportedOperation(f"setting {', '.join(unsupported)} is not supported",
                                   details={"properties": unsupported})  # fmt: skip
    for name in ("from", "sender"):
        address = odata.path_get(payload.get(name), "emailAddress/address")
        if isinstance(address, str) and address.casefold() != ctx.email().casefold():
            raise PermissionDenied("The user account which was used to submit this request "
                                   "does not have the right to send mail on behalf of the "
                                   "specified sending account.")  # fmt: skip


@dataclass
class Draft:
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    text: str = ""
    html: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    reply_to: str | None = None  # internal id of the message a createReply draft answers
    forward_of: str | None = None  # ... or a createForward draft forwards

    def apply(self, payload: Mapping[str, Any]) -> Draft:
        """Overlay the Graph message properties present in `payload`."""
        to, cc, bcc = (_addresses(payload, n) for n in ("toRecipients", "ccRecipients",
                                                         "bccRecipients"))  # fmt: skip
        self.to = self.to if to is None else to
        self.cc = self.cc if cc is None else cc
        self.bcc = self.bcc if bcc is None else bcc
        subject = _subject(payload)
        self.subject = self.subject if subject is None else subject
        content = _body(payload)
        if content is not None:
            self.text, self.html = content
        return self

    @classmethod
    def of(cls, m: CanonicalMessage) -> Draft:
        return cls([a.email for a in m.to], [a.email for a in m.cc], [a.email for a in m.bcc],
                   m.subject, m.body_text or "", m.body_html, m.in_reply_to,
                   list(m.references))  # fmt: skip

    def create(self, mb: AgentMailbox) -> str:
        out = mb.create_draft(to=self.to, subject=self.subject, text=self.text, cc=self.cc,
                              bcc=self.bcc, html=self.html, in_reply_to=self.in_reply_to,
                              references=self.references or None, reply_to=self.reply_to,
                              forward_of=self.forward_of)  # fmt: skip
        return str(out["message_id"])


# Action parameters are case-insensitive in Graph (the SDK sends `Message`, `Comment`,
# `DestinationId`, `SaveToSentItems`, `ToRecipients`): normalise them to camelCase.
ACTION_KEYS = {k.casefold(): k for k in ("message", "saveToSentItems", "comment", "toRecipients",
                                         "destinationId")}  # fmt: skip


def _args(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {ACTION_KEYS.get(k.casefold(), k): v for k, v in (payload or {}).items()}


def _action(ctx: Ctx, payload: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(normalised action parameters, their writable `message` object)."""
    data = _args(payload)
    message = _object(data, "message")
    check_writable(ctx, message)
    return data, message


def _object(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidRequest(f"{name} must be an object")
    return value


def _comment(payload: Mapping[str, Any]) -> str:
    value = payload.get("comment", "")
    if not isinstance(value, str):
        raise InvalidRequest("comment must be a string")
    return value


def _prefixed(prefix: str, subject: str, known: tuple[str, ...]) -> str:
    return subject if subject.lower().startswith(known) else f"{prefix} {subject}"


def reply_draft(ctx: Ctx, orig: CanonicalMessage, *, reply_all: bool, comment: str) -> Draft:
    """Recipients as Outlook computes them (and as the engine's direct reply does)."""
    me = ctx.email().casefold()
    to = [orig.from_.email] if orig.from_.email.casefold() != me else [a.email for a in orig.to]
    cc: list[str] = []
    if reply_all:
        to += [a.email for a in orig.to if a.email.casefold() != me]
        cc = [a.email for a in orig.cc if a.email.casefold() != me]
    to = [a for a in dict.fromkeys(to) if a.casefold() != me] or [orig.from_.email]
    return Draft(to, cc, [], _prefixed("RE:", orig.subject, ("re:",)), comment, None,
                 orig.message_id_header, [*orig.references, orig.message_id_header],
                 reply_to=orig.id)  # fmt: skip


def forward_draft(orig: CanonicalMessage, to: list[str], comment: str) -> Draft:
    quoted = (f"{comment}\n\n---------- Forwarded message ----------\nFrom: {orig.from_.email}\n"
              f"Subject: {orig.subject}\n\n{orig.body_text or ''}")  # fmt: skip
    return Draft(to, [], [], _prefixed("FW:", orig.subject, ("fw:", "fwd:")), quoted, None,
                 None, [*orig.references, orig.message_id_header], forward_of=orig.id)  # fmt: skip


# ---------------------------------------------------------------------------- user


@route("GET", "")
def get_user(ctx: Context) -> JSONResponse:
    fragment = "users/$entity"
    return ok({"@odata.context": context_url(ctx.request, fragment),
               **res.user(ctx.mailbox_id, ctx.email())})  # fmt: skip


# ------------------------------------------------------------------------ mail folders


def _folders(ctx: Ctx) -> list[dict[str, Any]]:
    counts = res.folder_counts(all_messages(ctx.mb, None))
    return [res.mail_folder(ctx.mailbox_id, f, counts) for f in ctx.mb.list_folders()]


@route("GET", "/mailFolders")
def list_folders(ctx: Context) -> JSONResponse:
    return ok({"@odata.context": context_url(ctx.request, "users('me')/mailFolders"),
               "value": _folders(ctx)})  # fmt: skip


@route("GET", "/mailFolders/{folder_id}")
def get_folder(folder_id: str, ctx: Context) -> JSONResponse:
    wanted = res.folder_id(ctx.mailbox_id, resolve_folder(ctx, folder_id).key)
    for doc in _folders(ctx):
        if doc["id"] == wanted:
            return ok({"@odata.context": context_url(ctx.request, "users('me')/mailFolders/$entity"),
                       **doc})  # fmt: skip
    raise NotFound("The specified folder could not be found in the store.")


@route("GET", "/mailFolders/{folder_id}/childFolders")
def child_folders(folder_id: str, ctx: Context) -> JSONResponse:
    folder_record(ctx, resolve_folder(ctx, folder_id))
    return ok({"@odata.context": context_url(ctx.request, "users('me')/mailFolders('id')/childFolders"),
               "value": []})  # fmt: skip


# ---------------------------------------------------------------------------- messages


def matching(ctx: Ctx, folder: FolderRef | None) -> list[tuple[CanonicalMessage, dict[str, Any]]]:
    """Messages selected by $search / $filter (unordered). The query is validated before
    the mailbox is read."""
    q = ctx.request.query_params
    search = kql.parse(q["$search"]) if "$search" in q else None
    if search is not None and ("$filter" in q or "$orderby" in q):
        raise odata.QueryNotSupported("$search cannot be combined with $filter or $orderby")
    predicate = odata.compile_filter(q["$filter"]) if "$filter" in q else None
    if search is not None:
        # plain text, or KQL whose positive free-text term narrows the candidates: the
        # engine search (audited as `search`); KQL on properties only: the folder listing
        anchor = search.text or search.anchor
        if anchor is not None:
            hits = ctx.mb.search_messages(anchor)
        else:
            hits = all_messages(ctx.mb, folder.engine_ref if folder else None)
        if search.node is not None:
            hits = [m for m in hits if kql.matches(search.node, m)]
        msgs = [m for m in hits if folder is None or res.folder_key(m.folder) == folder.key]
    else:
        msgs = all_messages(ctx.mb, folder.engine_ref if folder else None)
    docs = [(m, res.filter_view(m)) for m in msgs]
    return [d for d in docs if predicate(d[1])] if predicate else docs


def list_messages(ctx: Ctx, folder: FolderRef | None) -> JSONResponse:
    request, q = ctx.request, ctx.request.query_params
    check_options(request, LIST_OPTIONS)
    prefer = Prefer.parse(request)
    select, expand = read_options(request)
    top = odata.parse_int("$top", q.get("$top"), default=prefer.page_size or odata.DEFAULT_TOP,
                          low=1, high=odata.MAX_TOP)  # fmt: skip
    skip = odata.parse_int("$skip", q.get("$skip"), default=0, low=0, high=10**9)
    if "$skiptoken" in q:
        skip = odata.decode_token(q["$skiptoken"], ("o",))["o"]
    count = q.get("$count", "false").lower()
    if count not in ("true", "false"):
        raise odata.QueryError("$count must be true or false")
    if expand is not None and expand.attachments:
        top = min(top, MAX_TOP_WITH_ATTACHMENTS)  # bounds attachment bytes per response
    order = odata.parse_orderby(q["$orderby"]) if "$orderby" in q else [("receivedDateTime", True)]
    docs = ordered(matching(ctx, folder), order)
    page = docs[skip : skip + top]
    if expand is not None and expand.attachments:
        page = _within_attachment_budget(page)
    body: dict[str, Any] = {
        "@odata.context": context_url(request, "users('me')/messages"),
        "value": [
            render(request, m, select=select, prefer=prefer, expand=expand, mb=ctx.mb)
            for m, _ in page
        ],
    }
    if count == "true":
        body["@odata.count"] = len(docs)
    if skip + len(page) < len(docs):
        body["@odata.nextLink"] = link(request, {"$skip": str(skip + len(page))},
                                       ("$skiptoken",))  # fmt: skip
    return ok(body, headers=prefer.applied())


def _within_attachment_budget(page: list[Any]) -> list[Any]:
    """The longest prefix of `page` (at least one message) whose attachments total at most
    MAX_EXPANDED_ATTACHMENT_BYTES (sizes from metadata, before any bytes are read)."""
    total = 0
    for i, (m, _) in enumerate(page):
        total += sum(a.size for a in m.attachments)
        if total > MAX_EXPANDED_ATTACHMENT_BYTES and i > 0:
            return page[:i]
    return page


def count_messages(ctx: Ctx, folder: FolderRef | None) -> Response:
    check_options(ctx.request, COUNT_OPTIONS)
    return Response(str(len(matching(ctx, folder))), media_type="text/plain")


@route("GET", "/messages/$count")
def count_all_messages(ctx: Context) -> Response:
    return count_messages(ctx, None)


@route("GET", "/mailFolders/{folder_id}/messages/$count")
def count_folder_messages(folder_id: str, ctx: Context) -> Response:
    return count_messages(ctx, resolve_folder(ctx, folder_id))


@route("GET", "/messages")
def list_all_messages(ctx: Context) -> JSONResponse:
    return list_messages(ctx, None)


@route("GET", "/mailFolders/{folder_id}/messages")
def list_folder_messages(folder_id: str, ctx: Context) -> JSONResponse:
    return list_messages(ctx, resolve_folder(ctx, folder_id))


def _delta(ctx: Ctx, folder: FolderRef | None) -> JSONResponse:
    request, q = ctx.request, ctx.request.query_params
    check_options(request, DELTA_OPTIONS)
    prefer = Prefer.parse(request)
    select = odata.parse_select(q["$select"]) if "$select" in q else None
    size = odata.parse_int("$top", q.get("$top"), default=prefer.page_size or DELTA_DEFAULT_PAGE,
                           low=1, high=odata.MAX_TOP)  # fmt: skip
    size = min(size, ENGINE_PAGE)  # engine pages hold at most 500 items / events
    drop = ("$deltatoken", "$skiptoken")
    if "$deltatoken" in q:
        state = {**odata.decode_token(q["$deltatoken"], ("s",)), "m": "c"}
    elif "$skiptoken" in q:
        token = q["$skiptoken"]
        mode = odata.decode_token(token, ()).get("m")
        if mode not in ("i", "c"):
            raise odata.QueryError("malformed state token")
        state = odata.decode_token(token, ("a",) if mode == "i" else ("s",))
        if mode == "i" and not isinstance(state.get("c"), str):
            raise odata.QueryError("malformed state token")
    else:
        state = {"m": "i", "c": None, "a": ctx.mb.list_changes(0, limit=1)["latest_sequence"]}
    body: dict[str, Any] = {"@odata.context": context_url(request, "Collection(message)")}
    if state["m"] == "i":
        # keyset paging over (created, id): deleting or moving an already-returned message
        # cannot shift later pages; changes after the anchor arrive in the next round
        page = ctx.mb.list_messages(folder=folder.engine_ref if folder else None,
                                    page_size=size, cursor=state["c"])  # fmt: skip
        body["value"] = [render(request, m, select=select, prefer=prefer) for m in page.items]
        if page.next_cursor is not None:
            token = odata.encode_token({"m": "i", "c": page.next_cursor, "a": state["a"]})
            body["@odata.nextLink"] = link(request, {"$skiptoken": token}, drop)
        else:
            body["@odata.deltaLink"] = link(
                request, {"$deltatoken": odata.encode_token({"s": state["a"]})}, drop
            )
        return ok(body, headers=prefer.applied())
    feed = ctx.mb.list_changes(int(state["s"]), limit=size)  # bounds the per-round fan-out
    if feed["reset_since"]:
        raise SyncStateGone("The sync state is no longer valid; "
                            "restart the sync without a token.")  # fmt: skip
    ids = list(dict.fromkeys(c["message_id"] for c in feed["changes"] if c["message_id"]))
    present = ctx.mb.present_messages(ids)  # deleted ones are absent, not agent errors
    items = []
    for internal in ids:
        m = present.get(internal)
        if m is None or (folder is not None and res.folder_key(m.folder) != folder.key):
            items.append({"@odata.type": "#microsoft.graph.message", "id": graph_id(internal),
                          "@removed": {"reason": "deleted"}})  # fmt: skip
        else:
            items.append(render(request, m, select=select, prefer=prefer))
    body["value"] = items
    if feed["has_more"]:
        token = odata.encode_token({"m": "c", "s": feed["next_since"]})
        body["@odata.nextLink"] = link(request, {"$skiptoken": token}, drop)
    else:
        token = odata.encode_token({"s": feed["next_since"]})
        body["@odata.deltaLink"] = link(request, {"$deltatoken": token}, drop)
    return ok(body, headers=prefer.applied())


@route("GET", "/messages/delta()")  # the SDK calls the function form
@route("GET", "/messages/delta")
def messages_delta(ctx: Context) -> JSONResponse:
    return _delta(ctx, None)


@route("GET", "/mailFolders/{folder_id}/messages/delta()")
@route("GET", "/mailFolders/{folder_id}/messages/delta")
def folder_messages_delta(folder_id: str, ctx: Context) -> JSONResponse:
    return _delta(ctx, resolve_folder(ctx, folder_id))


@route("GET", "/messages/{message_id}")
def get_message(message_id: str, ctx: Context) -> JSONResponse:
    check_options(ctx.request, GET_OPTIONS)
    prefer = Prefer.parse(ctx.request)
    select, expand = read_options(ctx.request)
    m = ctx.mb.get_message(resolve_message(ctx, message_id))
    doc = render(ctx.request, m, select=select, prefer=prefer, expand=expand, mb=ctx.mb)
    return ok({"@odata.context": context_url(ctx.request, "users('me')/messages/$entity"), **doc},
              headers=prefer.applied())  # fmt: skip


@route("GET", "/messages/{message_id}/$value")
def get_mime(message_id: str, ctx: Context) -> Response:
    raw = ctx.mb.get_raw(resolve_message(ctx, message_id))
    if raw is None:  # pragma: no cover - every inserted message keeps its raw MIME
        raise NotFound("this message has no MIME content")
    return Response(raw, media_type="text/plain")


@route("POST", "/messages", 201)
def create_message(ctx: Context, payload: Payload = None) -> JSONResponse:
    data = _args(payload)
    check_writable(ctx, data)
    mid = Draft().apply(data).create(ctx.mb)
    return ok(render(ctx.request, ctx.mb.get_message(mid)), 201)


def _flag_changes(data: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    add: list[str] = []
    remove: list[str] = []
    if "isRead" in data:
        if not isinstance(data["isRead"], bool):
            raise InvalidRequest("isRead must be a boolean")
        (add if data["isRead"] else remove).append("SEEN")
    if "flag" in data:
        status = odata.path_get(data["flag"], "flagStatus")
        if status not in ("flagged", "notFlagged", "complete"):
            raise InvalidRequest("flag.flagStatus must be flagged, notFlagged or complete")
        (add if status == "flagged" else remove).append("FLAGGED")
    props = data.get("singleValueExtendedProperties")
    if props is not None:
        if not isinstance(props, list):
            raise InvalidRequest("singleValueExtendedProperties must be a collection")
        for prop in props:
            pid = odata.path_get(prop, "id")
            if not isinstance(pid, str) or odata.normalise_extended_id(pid) != res.LAST_VERB_KEY:
                raise UnsupportedOperation(f"extended property {pid!r} is not supported "
                                           f"(only {res.LAST_VERB_ID!r})")  # fmt: skip
            (add if str(odata.path_get(prop, "value")) in res.VERB_REPLIED else remove).append(
                "ANSWERED"
            )
    return add, remove


@route("PATCH", "/messages/{message_id}")
def update_message(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    """Applies, in order: draft content, flags (isRead/flag/replied marker), categories. Each
    is a separate engine write; on a failure the earlier ones stay applied (re-GET)."""
    data = _args(payload)
    mid = resolve_message(ctx, message_id)
    m = ctx.mb.get_message(mid)
    check_writable(ctx, {k: v for k, v in data.items() if k != "singleValueExtendedProperties"})
    content = [k for k in CONTENT_FIELDS if k in data]
    if content and not m.is_draft:
        raise InvalidRequest(f"{', '.join(content)} can only be updated on a draft (isDraft)")
    add, remove = _flag_changes(data)
    categories = data.get("categories")
    if categories is not None and (
        not isinstance(categories, list) or not all(isinstance(c, str) for c in categories)
    ):
        raise InvalidRequest("categories must be a collection of strings")
    current = res.user_categories(m)
    if content:
        # the engine replaces the draft's message (fresh flags DRAFT+SEEN, no labels): carry
        # the previous flags and categories over unless this PATCH changes them
        d = Draft.of(m).apply(data)
        out = ctx.mb.update_draft(mid, to=d.to, subject=d.subject, text=d.text, cc=d.cc,
                                  bcc=d.bcc, html=d.html)  # fmt: skip
        mid = str(out["message_id"])
        carried = sorted(set(m.flags) - {"DRAFT", "SEEN"} - set(remove))
        add = list(dict.fromkeys([*carried, *add]))
        if "SEEN" not in m.flags and "SEEN" not in add:
            remove = list(dict.fromkeys([*remove, "SEEN"]))
        if categories is None:
            categories = current
        current = []
    if add or remove:
        ctx.mb.set_flags(mid, add=add, remove=remove)
    if categories is not None:
        wanted = list(dict.fromkeys(categories))
        plus = [c for c in wanted if c not in current]
        minus = [c for c in current if c not in wanted]
        if plus or minus:
            ctx.mb.modify_labels(mid, add=plus, remove=minus)
    return ok(render(ctx.request, ctx.mb.get_message(mid)))


@route("DELETE", "/messages/{message_id}", 204)
def delete_message(message_id: str, ctx: Context) -> Response:
    """Graph semantics: DELETE moves the message to Deleted Items; deleting from Deleted Items
    removes it (use permanentDelete to remove directly)."""
    mid = resolve_message(ctx, message_id)
    m = ctx.mb.get_message(mid)
    if res.folder_key(m.folder) == "trash":
        ctx.mb.delete(mid)
    else:
        ctx.mb.trash(mid)
    return Response(status_code=204)


@route("POST", "/messages/{message_id}/permanentDelete", 204)
def permanent_delete(message_id: str, ctx: Context) -> Response:
    ctx.mb.delete(resolve_message(ctx, message_id))
    return Response(status_code=204)


def _destination(ctx: Ctx, data: Mapping[str, Any]) -> str:
    dest = data.get("destinationId")
    if not isinstance(dest, str) or not dest:
        raise InvalidRequest("destinationId is required")
    return resolve_folder(ctx, dest).engine_ref


@route("POST", "/messages/{message_id}/move", 201)
def move_message(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    mid = resolve_message(ctx, message_id)
    ctx.mb.move(mid, _destination(ctx, _args(payload)))
    return ok(render(ctx.request, ctx.mb.get_message(mid)), 201)


@route("POST", "/messages/{message_id}/copy", 201)
def copy_message(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    mid = resolve_message(ctx, message_id)
    out = ctx.mb.copy(mid, _destination(ctx, _args(payload)))
    return ok(render(ctx.request, ctx.mb.get_message(str(out["message_id"]))), 201)


# ------------------------------------------------------------------------------ sending


@route("POST", "/sendMail", 202)
def send_mail(ctx: Context, payload: Payload = None) -> Response:
    """`saveToSentItems` is accepted but a Sent Items copy is always kept (documented)."""
    data, message = _action(ctx, payload)
    if not message:
        raise InvalidRequest("message is required")
    save = data.get("saveToSentItems", True)
    if not isinstance(save, bool):
        raise InvalidRequest("saveToSentItems must be a boolean")
    d = Draft().apply(message)
    ctx.mb.send(to=d.to, subject=d.subject, text=d.text, cc=d.cc, bcc=d.bcc, html=d.html)
    return accepted()


@route("POST", "/messages/{message_id}/send", 202)
def send_draft(message_id: str, ctx: Context) -> Response:
    """Draft -> sent: the draft leaves Drafts and a copy lands in Sent Items. A createReply /
    createReplyAll / createForward draft is sent as the engine's `reply` / `forward`."""
    ctx.mb.send_draft(resolve_message(ctx, message_id))
    return accepted()


def _create_response(ctx: Ctx, d: Draft) -> JSONResponse:
    return ok(render(ctx.request, ctx.mb.get_message(d.create(ctx.mb))), 201)


@route("POST", "/messages/{message_id}/createReply", 201)
def create_reply(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    return _create_reply(message_id, ctx, payload, reply_all=False)


@route("POST", "/messages/{message_id}/createReplyAll", 201)
def create_reply_all(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    return _create_reply(message_id, ctx, payload, reply_all=True)


def _create_reply(message_id: str, ctx: Ctx, payload: dict[str, Any] | None, *,
                  reply_all: bool) -> JSONResponse:  # fmt: skip
    data, message = _action(ctx, payload)
    orig = ctx.mb.get_message(resolve_message(ctx, message_id))
    draft = reply_draft(ctx, orig, reply_all=reply_all, comment=_comment(data))
    return _create_response(ctx, draft.apply(message))


@route("POST", "/messages/{message_id}/createForward", 201)
def create_forward(message_id: str, ctx: Context, payload: Payload = None) -> JSONResponse:
    data, message = _action(ctx, payload)
    orig = ctx.mb.get_message(resolve_message(ctx, message_id))
    draft = forward_draft(orig, _addresses(data, "toRecipients") or [], _comment(data))
    return _create_response(ctx, draft.apply(message))


def _engine_only(message: Mapping[str, Any], allowed: tuple[str, ...]) -> None:
    """Direct reply/forward run as the engine's own operation (scenario permissions,
    triggers and budget see exactly one `reply` / `forward`). Message overrides the engine
    cannot express are refused rather than emulated with draft + send."""
    extra = sorted(k for k, v in message.items() if k not in allowed and v not in (None, [], ""))
    if extra:
        raise UnsupportedOperation(
            f"message properties {', '.join(extra)} are not supported on this action; use "
            "createReply/createForward, PATCH the draft, then send it",
            details={"properties": extra},
        )


REPLY_OVERRIDES = ("toRecipients", "ccRecipients", "bccRecipients", "subject", "body")


def _reply(
    message_id: str, ctx: Ctx, payload: dict[str, Any] | None, *, reply_all: bool
) -> Response:
    """One engine `reply`; `message` recipients/subject/body override the derived reply."""
    data, message = _action(ctx, payload)
    _engine_only(message, REPLY_OVERRIDES)
    comment = _comment(data)
    to, cc, bcc = (_addresses(message, n) for n in REPLY_OVERRIDES[:3])
    text, html = comment, None
    content = _body(message)
    if content is not None:
        text = f"{comment}\n\n{content[0]}" if comment else content[0]
        html = content[1]
        if html is not None and comment:
            html = f"<p>{escape(comment)}</p>{html}"
    ctx.mb.reply(resolve_message(ctx, message_id), text=text, reply_all=reply_all, to=to, cc=cc,
                 bcc=bcc, subject=_subject(message), html=html)  # fmt: skip
    return accepted()


@route("POST", "/messages/{message_id}/reply", 202)
def reply(message_id: str, ctx: Context, payload: Payload = None) -> Response:
    return _reply(message_id, ctx, payload, reply_all=False)


@route("POST", "/messages/{message_id}/replyAll", 202)
def reply_all(message_id: str, ctx: Context, payload: Payload = None) -> Response:
    return _reply(message_id, ctx, payload, reply_all=True)


@route("POST", "/messages/{message_id}/forward", 202)
def forward(message_id: str, ctx: Context, payload: Payload = None) -> Response:
    """The engine's forward keeps the original's attachments."""
    data, message = _action(ctx, payload)
    _engine_only(message, ("toRecipients",))
    to = _addresses(data, "toRecipients") or _addresses(message, "toRecipients") or []
    if not to:
        raise InvalidRequest("toRecipients is required to forward a message")
    ctx.mb.forward(resolve_message(ctx, message_id), to=to, text=_comment(data))
    return accepted()


# --------------------------------------------------------------------------- attachments


def _attachment(ctx: Ctx, message_id: str, attachment_id: str) -> tuple[CanonicalMessage, int]:
    m = ctx.mb.get_message(resolve_message(ctx, message_id))
    for i, att in enumerate(m.attachments):
        if res.attachment_id(att) == attachment_id:
            return m, i
    raise NotFound(ITEM_NOT_FOUND, details={"id": attachment_id})


@route("GET", "/messages/{message_id}/attachments")
def list_attachments(message_id: str, ctx: Context) -> JSONResponse:
    m = ctx.mb.get_message(resolve_message(ctx, message_id))
    return ok({"@odata.context": context_url(ctx.request, "users('me')/messages('id')/attachments"),
               "value": attachment_docs(ctx.mb, m)})  # fmt: skip


@route("GET", "/messages/{message_id}/attachments/{attachment_id}")
def get_attachment(message_id: str, attachment_id: str, ctx: Context) -> JSONResponse:
    m, i = _attachment(ctx, message_id, attachment_id)
    ref, data = ctx.mb.get_attachment(m.id, m.attachments[i].id)
    doc = res.attachment(ref, m, data)
    return ok({"@odata.context": context_url(ctx.request, "users('me')/messages('id')/attachments/$entity"),
               **doc})  # fmt: skip


@route("GET", "/messages/{message_id}/attachments/{attachment_id}/$value")
def get_attachment_content(message_id: str, attachment_id: str, ctx: Context) -> Response:
    m, i = _attachment(ctx, message_id, attachment_id)
    ref, data = ctx.mb.get_attachment(m.id, m.attachments[i].id)
    return Response(data, headers=safe_download_headers(ref.filename, ref.mime_type))


# ------------------------------------------------------------------- capability discovery


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                  include_in_schema=False, response_model=None)  # fmt: skip
def unsupported(path: str, ctx: Context) -> NoReturn:
    """Anything not listed above fails explicitly (spec 5.5 capability discovery)."""
    raise UnsupportedOperation(
        f"{ctx.request.method} /{path} is not supported by this server",
        details={"path": path},
    )
