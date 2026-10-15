"""Agent-facing neutral mailbox API (spec 7.3). Agent credentials are bound to one run +
mailbox; the path mailbox must match. No scenario/evaluation data is reachable here.
Owner: WS-A.

Every handler goes through the `AgentMailbox` facade bound to the credential's run and
mailbox, so the engine's agent guards (allowed operations, run active, write budget) apply.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from mailsim.api import schemas as s
from mailsim.api.deps import Agent
from mailsim.api.errors import ErrorEnvelope
from mailsim.api.pagination import page_messages
from mailsim.api.routers._common import agent
from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.domain.models import CanonicalMessage
from mailsim.engine.facades import AgentMailbox
from mailsim.engine.pagination import check_page_size

router = APIRouter(prefix="/v1/mailboxes/{mailbox_id}", tags=["mailbox"],
                   responses={"default": {"model": ErrorEnvelope}})  # fmt: skip


def _folder_name(mb: AgentMailbox, folder: str) -> str:
    for f in mb.list_folders():  # same matching rules as the engine: id, system type or name
        if folder in (f["id"], f["system_type"]) or folder.lower() == f["name"].lower():
            return str(f["name"])
    raise NotFound(f"folder {folder!r} not found")


@router.get("/messages", response_model=s.MessagePage)
def list_messages(request: Request, mailbox_id: str, principal: Agent, folder: str | None = None,
                  q: str | None = None, cursor: str | None = None,
                  page_size: Annotated[int, Query(ge=1, le=500)] = 50) -> s.MessagePage:  # fmt: skip
    """List (or with `q`, search subject/body/sender) ordered by (created_at, id).
    Follow `next_cursor` until it is null; under a fixed state no item is skipped or repeated."""
    mb = agent(request, principal)
    if q is None:
        page = mb.list_messages(folder=folder, page_size=page_size, cursor=cursor)
    else:
        check_page_size(page_size)
        hits = mb.search_messages(q)
        if folder is not None:
            name = _folder_name(mb, folder)
            hits = [h for h in hits if h.folder == name]
        page = page_messages(hits, page_size, cursor)
    return s.MessagePage(items=page.items, next_cursor=page.next_cursor)


@router.get("/messages/{message_id}", response_model=CanonicalMessage)
def get_message(
    request: Request, mailbox_id: str, message_id: str, principal: Agent
) -> CanonicalMessage:
    return agent(request, principal).get_message(message_id)


@router.get("/threads/{thread_id}", response_model=list[CanonicalMessage])
def get_thread(
    request: Request, mailbox_id: str, thread_id: str, principal: Agent
) -> list[CanonicalMessage]:
    return agent(request, principal).get_thread(thread_id)


@router.post("/messages/{message_id}/reply", response_model=s.SendResult, status_code=201)
def reply(
    request: Request, mailbox_id: str, message_id: str, body: s.ReplyRequest, principal: Agent
) -> s.SendResult:
    out = agent(request, principal).reply(message_id, text=body.text, reply_all=body.reply_all)
    return s.SendResult.model_validate(out)


@router.post("/messages", response_model=s.SendResult, status_code=201)
def send(request: Request, mailbox_id: str, body: s.SendRequest, principal: Agent) -> s.SendResult:
    """Send to simulated mailboxes only. External recipients follow the scenario's rule
    (deny/sink/event); nothing is ever delivered outside the sandbox."""
    out = agent(request, principal).send(to=body.to, cc=body.cc, bcc=body.bcc,
                                         subject=body.subject, text=body.text, html=body.html)  # fmt: skip
    return s.SendResult.model_validate(out)


@router.patch("/messages/{message_id}", response_model=CanonicalMessage)
def patch_message(
    request: Request, mailbox_id: str, message_id: str, body: s.MessagePatch, principal: Agent
) -> CanonicalMessage:
    """Move and/or change labels and flags. Each kind of change is one agent write (budget)."""
    if not (body.folder or body.add_labels or body.remove_labels or body.add_flags
            or body.remove_flags):  # fmt: skip
        raise InvalidRequest("empty patch: set folder, labels or flags")
    mb = agent(request, principal)
    if body.folder is not None:
        mb.move(message_id, body.folder)
    if body.add_labels or body.remove_labels:
        mb.modify_labels(message_id, add=body.add_labels, remove=body.remove_labels)
    if body.add_flags or body.remove_flags:
        mb.set_flags(message_id, add=body.add_flags, remove=body.remove_flags)
    return mb.get_message(message_id)


@router.delete("/messages/{message_id}", status_code=204, response_class=Response)
def delete_message(
    request: Request, mailbox_id: str, message_id: str, principal: Agent
) -> Response:
    """Permanently delete a message (to move it to Trash, PATCH `folder: trash`)."""
    agent(request, principal).delete(message_id)
    return Response(status_code=204)


@router.get("/changes", response_model=s.ChangeFeed)
def changes(
    request: Request,
    mailbox_id: str,
    principal: Agent,
    since: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> s.ChangeFeed:
    """Mailbox change feed after event `since`; resume from `next_since` while `has_more`.
    `reset_since` true means the run was reset: resynchronise fully."""
    return s.ChangeFeed.model_validate(agent(request, principal).list_changes(since, limit=limit))


@router.get("/folders", response_model=list[s.FolderOut])
def folders(request: Request, mailbox_id: str, principal: Agent) -> list[s.FolderOut]:
    return [
        s.FolderOut(id=f["id"], name=f["name"], system_type=f["system_type"])
        for f in agent(request, principal).list_folders()
    ]
