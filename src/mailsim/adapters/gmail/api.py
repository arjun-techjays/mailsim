"""Gmail API v1 compatibility profile (spec 5.1), mounted by ``mailsim.serve.mount_adapters``.

Paths mirror Google's: ``/gmail/v1/users/{userId}/...`` and the media-upload variants under
``/upload/gmail/v1/users/{userId}/...``. ``userId`` is ``me`` or the mailbox address. Every
route requires an agent key and reaches the engine only through that agent's
``AgentMailbox``; errors use Google's error shape (see ``errors.py``). The compatibility
profile, including deviations, is documented in ``docs/compat/gmail.md``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from mailsim.adapters.gmail.auth import Agent, Gmail
from mailsim.adapters.gmail.errors import GmailRoute
from mailsim.adapters.gmail.fields import select
from mailsim.adapters.gmail.labels import message_label_ids
from mailsim.adapters.gmail.resources import decode_raw, stub
from mailsim.adapters.gmail.service import GmailService
from mailsim.adapters.gmail.uploads import read_upload
from mailsim.domain.errors import InvalidRequest, NotFound, UnsupportedOperation
from mailsim.domain.models import MAX_LABEL_CHARS, MAX_LABELS_PER_CHANGE, CanonicalMessage
from mailsim.engine.context import use_request_item

BASE = "/gmail/v1/users/{user_id}"
UPLOAD = "/upload/gmail/v1/users/{user_id}"
MAX_BATCH = 1000

router = APIRouter(route_class=GmailRoute, tags=["gmail"])

JsonBody = Annotated[dict[str, Any] | None, Body()]
Strings = Annotated[list[str] | None, Query()]


def _out(request: Request, data: dict[str, Any]) -> JSONResponse:
    return JSONResponse(select(data, request.query_params.get("fields")))


def _no_content() -> Response:
    return Response(status_code=204)


def _strings(body: dict[str, Any], key: str) -> list[str]:
    value = body.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidRequest(f"'{key}' must be a list of strings")
    return value


# per request list; a user label id is "Label_" + the hex of a name's UTF-8 (<= 4 bytes/char)
MAX_LABEL_IDS = MAX_LABELS_PER_CHANGE
MAX_LABEL_ID_CHARS = len("Label_") + 8 * MAX_LABEL_CHARS


def _label_ids(body: dict[str, Any], key: str) -> list[str]:
    value = _strings(body, key)
    if len(value) > MAX_LABEL_IDS or any(len(v) > MAX_LABEL_ID_CHARS for v in value):
        raise InvalidRequest(f"'{key}' takes at most {MAX_LABEL_IDS} label ids of at most "
                             f"{MAX_LABEL_ID_CHARS} characters")  # fmt: skip
    return value


def _thread_id(body: dict[str, Any]) -> str | None:
    value = body.get("threadId")
    if value is not None and not isinstance(value, str):
        raise InvalidRequest("'threadId' must be a string")
    return value or None


def _message_part(body: dict[str, Any]) -> dict[str, Any]:
    message = body.get("message")
    if not isinstance(message, dict):
        raise InvalidRequest("'message' is required")
    return message


def _labelled(msg: CanonicalMessage) -> dict[str, Any]:
    return {**stub(msg), "labelIds": message_label_ids(msg)}


def _ids(body: dict[str, Any]) -> list[str]:
    ids = _strings(body, "ids")
    if len(ids) > MAX_BATCH:
        raise InvalidRequest(f"at most {MAX_BATCH} ids per batch request")
    return ids


# ------------------------------------------------------------------------------- profile


@router.get(BASE + "/profile")
def get_profile(request: Request, user_id: str, gmail: Gmail) -> Response:
    return _out(request, gmail.profile())


# ------------------------------------------------------------------------------ messages


@router.get(BASE + "/messages")
def list_messages(request: Request, user_id: str, gmail: Gmail, q: str | None = None,
                  labelIds: Strings = None, maxResults: int | None = None,
                  pageToken: str | None = None, includeSpamTrash: bool = False) -> Response:  # fmt: skip
    return _out(request, gmail.list_messages(q, labelIds or [], maxResults, pageToken,
                                             includeSpamTrash))  # fmt: skip


@router.get(BASE + "/messages/{message_id}")
def get_message(request: Request, user_id: str, message_id: str, gmail: Gmail,
                format: str = "full", metadataHeaders: Strings = None) -> Response:  # fmt: skip
    return _out(request, gmail.resource(gmail.message(message_id), format, metadataHeaders))


def _insert(gmail: GmailService, body: dict[str, Any], raw: bytes | None) -> dict[str, Any]:
    data = raw if raw is not None else decode_raw(body.get("raw"))
    return _labelled(gmail.insert(data, _label_ids(body, "labelIds")))


@router.post(BASE + "/messages")
@router.post(BASE + "/messages/import")
def insert_message(request: Request, user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    return _out(request, _insert(gmail, body or {}, None))


def _send(gmail: GmailService, body: dict[str, Any], raw: bytes | None) -> dict[str, Any]:
    data = raw if raw is not None else decode_raw(body.get("raw"))
    return _labelled(gmail.send(data, _thread_id(body)))


@router.post(BASE + "/messages/send")
def send_message(request: Request, user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    return _out(request, _send(gmail, body or {}, None))


@router.post(BASE + "/messages/{message_id}/modify")
def modify_message(request: Request, user_id: str, message_id: str, gmail: Gmail,
                   body: JsonBody = None) -> Response:  # fmt: skip
    body = body or {}
    msg = gmail.modify(gmail.message(message_id), _label_ids(body, "addLabelIds"),
                       _label_ids(body, "removeLabelIds"))  # fmt: skip
    return _out(request, _labelled(msg))


@router.post(BASE + "/messages/batchModify")
def batch_modify(user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    body = body or {}
    add, remove = _label_ids(body, "addLabelIds"), _label_ids(body, "removeLabelIds")
    for gid in _ids(body):
        with use_request_item():  # each id's reads count until its own write (read caps)
            gmail.modify(gmail.message(gid), add, remove)
    return _no_content()


@router.post(BASE + "/messages/batchDelete")
def batch_delete(user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    for gid in _ids(body or {}):
        with use_request_item():
            gmail.delete(gmail.message(gid))
    return _no_content()


@router.post(BASE + "/messages/{message_id}/trash")
def trash_message(request: Request, user_id: str, message_id: str, gmail: Gmail) -> Response:
    return _out(request, _labelled(gmail.trash(gmail.message(message_id))))


@router.post(BASE + "/messages/{message_id}/untrash")
def untrash_message(request: Request, user_id: str, message_id: str, gmail: Gmail) -> Response:
    return _out(request, _labelled(gmail.trash(gmail.message(message_id), restore=True)))


@router.delete(BASE + "/messages/{message_id}")
def delete_message(user_id: str, message_id: str, gmail: Gmail) -> Response:
    gmail.delete(gmail.message(message_id))
    return _no_content()


@router.get(BASE + "/messages/{message_id}/attachments/{attachment_id}")
def get_attachment(request: Request, user_id: str, message_id: str, attachment_id: str,
                   gmail: Gmail) -> Response:  # fmt: skip
    return _out(request, gmail.attachment(message_id, attachment_id))


# ------------------------------------------------------------------------------- threads


@router.get(BASE + "/threads")
def list_threads(request: Request, user_id: str, gmail: Gmail, q: str | None = None,
                 labelIds: Strings = None, maxResults: int | None = None,
                 pageToken: str | None = None, includeSpamTrash: bool = False) -> Response:  # fmt: skip
    return _out(request, gmail.list_threads(q, labelIds or [], maxResults, pageToken,
                                            includeSpamTrash))  # fmt: skip


@router.get(BASE + "/threads/{thread_id}")
def get_thread(request: Request, user_id: str, thread_id: str, gmail: Gmail,
               format: str = "full", metadataHeaders: Strings = None) -> Response:  # fmt: skip
    return _out(request, gmail.get_thread(thread_id, format, metadataHeaders))


def _thread_summary(gmail: GmailService, thread_id: str) -> dict[str, Any]:
    msgs = gmail.thread(thread_id)
    return {
        "id": thread_id,
        "historyId": gmail.history_id(),
        "messages": [_labelled(x) for x in msgs],
    }


@router.post(BASE + "/threads/{thread_id}/modify")
def modify_thread(request: Request, user_id: str, thread_id: str, gmail: Gmail,
                  body: JsonBody = None) -> Response:  # fmt: skip
    body = body or {}
    add, remove = _label_ids(body, "addLabelIds"), _label_ids(body, "removeLabelIds")
    for msg in gmail.thread(thread_id):
        with use_request_item():
            gmail.modify(msg, add, remove)
    return _out(request, _thread_summary(gmail, thread_id))


@router.post(BASE + "/threads/{thread_id}/trash")
@router.post(BASE + "/threads/{thread_id}/untrash")
def trash_thread(request: Request, user_id: str, thread_id: str, gmail: Gmail) -> Response:
    restore = request.url.path.endswith("/untrash")
    for msg in gmail.thread(thread_id):
        with use_request_item():
            gmail.trash(msg, restore=restore)
    return _out(request, _thread_summary(gmail, thread_id))


@router.delete(BASE + "/threads/{thread_id}")
def delete_thread(user_id: str, thread_id: str, gmail: Gmail) -> Response:
    for msg in gmail.thread(thread_id):
        with use_request_item():
            gmail.delete(msg)
    return _no_content()


# -------------------------------------------------------------------------------- labels


@router.get(BASE + "/labels")
def list_labels(request: Request, user_id: str, gmail: Gmail) -> Response:
    return _out(request, gmail.list_labels())


@router.post(BASE + "/labels")
def create_label(request: Request, user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    return _out(request, gmail.create_label(body or {}))


@router.get(BASE + "/labels/{label_id}")
def get_label(request: Request, user_id: str, label_id: str, gmail: Gmail) -> Response:
    return _out(request, gmail.get_label(label_id))


@router.put(BASE + "/labels/{label_id}")
@router.patch(BASE + "/labels/{label_id}")
def update_label(request: Request, user_id: str, label_id: str, gmail: Gmail,
                 body: JsonBody = None) -> Response:  # fmt: skip
    return _out(request, gmail.update_label(label_id, body or {}, request.method == "PATCH"))


@router.delete(BASE + "/labels/{label_id}")
def delete_label(user_id: str, label_id: str, gmail: Gmail) -> Response:
    gmail.delete_label(label_id)
    return _no_content()


# -------------------------------------------------------------------------------- drafts


@router.get(BASE + "/drafts")
def list_drafts(request: Request, user_id: str, gmail: Gmail, q: str | None = None,
                maxResults: int | None = None, pageToken: str | None = None,
                includeSpamTrash: bool = False) -> Response:  # fmt: skip
    return _out(request, gmail.list_drafts(q, maxResults, pageToken, includeSpamTrash))


@router.get(BASE + "/drafts/{draft_id}")
def get_draft(request: Request, user_id: str, draft_id: str, gmail: Gmail,
              format: str = "full") -> Response:  # fmt: skip
    return _out(request, gmail.draft_resource(gmail.draft(draft_id), format))


def _create_draft(gmail: GmailService, body: dict[str, Any], raw: bytes | None) -> dict[str, Any]:
    message = _message_part(body) if raw is None or "message" in body else {}
    data = raw if raw is not None else decode_raw(message.get("raw"))
    return gmail.draft_resource(gmail.create_draft(data, _thread_id(message)))


@router.post(BASE + "/drafts")
def create_draft(request: Request, user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    return _out(request, _create_draft(gmail, body or {}, None))


def _update_draft(gmail: GmailService, draft_id: str, body: dict[str, Any],
                  raw: bytes | None) -> dict[str, Any]:  # fmt: skip
    data = raw if raw is not None else decode_raw(_message_part(body).get("raw"))
    return gmail.draft_resource(gmail.update_draft(draft_id, data))


@router.put(BASE + "/drafts/{draft_id}")
def update_draft(request: Request, user_id: str, draft_id: str, gmail: Gmail,
                 body: JsonBody = None) -> Response:  # fmt: skip
    return _out(request, _update_draft(gmail, draft_id, body or {}, None))


@router.delete(BASE + "/drafts/{draft_id}")
def delete_draft(user_id: str, draft_id: str, gmail: Gmail) -> Response:
    gmail.delete(gmail.draft(draft_id))
    return _no_content()


def _send_draft(gmail: GmailService, body: dict[str, Any], raw: bytes | None) -> dict[str, Any]:
    draft_id = body.get("id")
    if not isinstance(draft_id, str) or not draft_id:
        raise InvalidRequest("'id' (the draft id) is required")
    message = _message_part(body) if "message" in body else {}
    if raw is None and message.get("raw"):
        raw = decode_raw(message["raw"])
    return _labelled(gmail.send_draft(draft_id, raw))


@router.post(BASE + "/drafts/send")
def send_draft(request: Request, user_id: str, gmail: Gmail, body: JsonBody = None) -> Response:
    return _out(request, _send_draft(gmail, body or {}, None))


# ------------------------------------------------------------------------------- history


@router.get(BASE + "/history")
def list_history(request: Request, user_id: str, gmail: Gmail,
                 startHistoryId: str | None = None, maxResults: int | None = None,
                 pageToken: str | None = None, labelId: str | None = None,
                 historyTypes: Strings = None) -> Response:  # fmt: skip
    return _out(request, gmail.history(startHistoryId, maxResults, pageToken, labelId,
                                       historyTypes or []))  # fmt: skip


# ------------------------------------------------------------------------- media uploads

_UPLOADS = {
    "messages": _insert,
    "messages/import": _insert,
    "messages/send": _send,
    "drafts": _create_draft,
    "drafts/send": _send_draft,
}


@router.post(UPLOAD + "/messages")
@router.post(UPLOAD + "/messages/import")
@router.post(UPLOAD + "/messages/send")
@router.post(UPLOAD + "/drafts")
@router.post(UPLOAD + "/drafts/send")
async def upload(request: Request, user_id: str, gmail: Gmail,
                 uploadType: str | None = None) -> Response:  # fmt: skip
    action = request.scope["route"].path.removeprefix(UPLOAD + "/")
    meta, raw = await run_in_threadpool(
        read_upload, uploadType, request.headers.get("content-type", ""), await request.body()
    )
    data = await run_in_threadpool(_UPLOADS[action], gmail, meta, raw)
    return _out(request, data)


@router.put(UPLOAD + "/drafts/{draft_id}")
async def upload_draft_update(request: Request, user_id: str, draft_id: str, gmail: Gmail,
                              uploadType: str | None = None) -> Response:  # fmt: skip
    meta, raw = await run_in_threadpool(
        read_upload, uploadType, request.headers.get("content-type", ""), await request.body()
    )
    return _out(request, await run_in_threadpool(_update_draft, gmail, draft_id, meta, raw))


# ---------------------------------------------------------------------- everything else

_UNSUPPORTED = ("settings", "watch", "stop")
_ANY = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/gmail/v1/{rest:path}", methods=_ANY)
@router.api_route("/upload/gmail/v1/{rest:path}", methods=_ANY)
def not_in_profile(request: Request, rest: str, principal: Agent) -> Response:
    """Capability discovery: Gmail features outside this profile fail explicitly (501); paths
    that do not exist in Gmail answer 404."""
    parts = rest.split("/")
    if len(parts) >= 3 and parts[0] == "users" and parts[2] in _UNSUPPORTED:
        raise UnsupportedOperation(f"{request.method} {request.url.path} is not supported by "
                                   "this server")  # fmt: skip
    raise NotFound(f"{request.method} {request.url.path} not found")
