"""Agent-facing neutral mailbox API (spec 7.3). Agent credentials are bound to one run +
mailbox; the path mailbox must match. No scenario/evaluation data is reachable here.
Owner: WS-A."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from mailsim.api import schemas as s
from mailsim.api.deps import get_principal, not_implemented
from mailsim.api.errors import ErrorEnvelope
from mailsim.auth.principal import Principal
from mailsim.domain.models import CanonicalMessage

router = APIRouter(prefix="/v1/mailboxes/{mailbox_id}", tags=["mailbox"],
                   responses={"default": {"model": ErrorEnvelope}})  # fmt: skip
P = Annotated[Principal, Depends(get_principal)]


@router.get("/messages", response_model=s.MessagePage)
def list_messages(mailbox_id: str, principal: P, folder: str | None = None, q: str | None = None,
                  cursor: str | None = None,
                  page_size: Annotated[int, Query(ge=1, le=500)] = 50) -> s.MessagePage:  # fmt: skip
    raise not_implemented("WS-A")


@router.get("/messages/{message_id}", response_model=CanonicalMessage)
def get_message(mailbox_id: str, message_id: str, principal: P) -> CanonicalMessage:
    raise not_implemented("WS-A")


@router.get("/threads/{thread_id}", response_model=list[CanonicalMessage])
def get_thread(mailbox_id: str, thread_id: str, principal: P) -> list[CanonicalMessage]:
    raise not_implemented("WS-A")


@router.post("/messages/{message_id}/reply", response_model=s.SendResult, status_code=201)
def reply(mailbox_id: str, message_id: str, body: s.ReplyRequest, principal: P) -> s.SendResult:
    raise not_implemented("WS-A")


@router.post("/messages", response_model=s.SendResult, status_code=201)
def send(mailbox_id: str, body: s.SendRequest, principal: P) -> s.SendResult:
    raise not_implemented("WS-A")


@router.patch("/messages/{message_id}", response_model=CanonicalMessage)
def patch_message(
    mailbox_id: str, message_id: str, body: s.MessagePatch, principal: P
) -> CanonicalMessage:
    raise not_implemented("WS-A")


@router.get("/folders", response_model=list[s.FolderOut])
def folders(mailbox_id: str, principal: P) -> list[s.FolderOut]:
    raise not_implemented("WS-A")
