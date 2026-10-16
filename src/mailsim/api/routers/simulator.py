"""Simulator control (spec 7.2). Controller/evaluator credentials only. Owner: WS-A."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response

from mailsim.api import schemas as s
from mailsim.api.deps import Controller, Inspector
from mailsim.api.errors import ErrorEnvelope
from mailsim.api.idempotency import idempotent
from mailsim.api.routers._common import (
    ATTACHMENT_RESPONSE,
    attachment_response,
    controller,
    inspector,
    run_personas,
)
from mailsim.domain.errors import InvalidRequest, PermissionDenied, UnsupportedOperation
from mailsim.engine.facades import EvaluatorView

router = APIRouter(prefix="/v1/runs/{run_id}", tags=["simulator"],
                   responses={"default": {"model": ErrorEnvelope}})  # fmt: skip
Idem = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


def _message(body: s.InjectRequest) -> dict[str, Any] | bytes:
    if (body.message is None) == (body.raw_base64 is None):
        raise InvalidRequest("provide exactly one of `message` or `raw_base64`")
    if body.message is not None:
        return body.message.model_dump(by_alias=True, exclude_none=True)
    assert body.raw_base64 is not None
    try:
        return base64.b64decode(body.raw_base64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidRequest("raw_base64 is not valid base64") from None


@router.post("/mailboxes/{mailbox_id}/inject", response_model=s.InjectResult, status_code=201)
def inject(
    request: Request,
    run_id: str,
    mailbox_id: str,
    body: s.InjectRequest,
    principal: Controller,
    idempotency_key: Idem = None,
) -> Response:
    """Inject an inbound message into a simulated mailbox. Nothing ever leaves the sandbox:
    the only delivery status is `delivered_to_simulated_mailbox`."""
    mode = body.delivery.get("mode", "immediate")
    if mode != "immediate":
        raise UnsupportedOperation(f"delivery mode {mode!r} is not supported; use 'immediate'",
                                   details={"supported": ["immediate"]})  # fmt: skip
    message = _message(body)

    def run() -> s.InjectResult:
        out = controller(request, principal).inject(run_id, mailbox_id, message,
                                                    event_id=body.event_id)  # fmt: skip
        return s.InjectResult.model_validate(out)

    payload = body.model_dump(mode="json", by_alias=True)
    return idempotent(request, principal, idempotency_key, payload, 201, run)


@router.post("/clock/advance", response_model=s.ClockResult)
def advance(
    request: Request,
    run_id: str,
    body: s.AdvanceRequest,
    principal: Controller,
    idempotency_key: Idem = None,
) -> Response:
    def run() -> s.ClockResult:
        ctl = controller(request, principal)
        result = ctl.advance(run_id, body.seconds)
        return s.ClockResult.model_validate(
            {**result, "persona_replies": run_personas(request, ctl, run_id)}
        )

    return idempotent(request, principal, idempotency_key, body.model_dump(mode="json"), 200, run)


@router.post("/scheduler/tick", response_model=s.ClockResult)
def tick(request: Request, run_id: str, principal: Controller) -> s.ClockResult:
    ctl = controller(request, principal)
    result = ctl.tick(run_id)
    return s.ClockResult.model_validate(
        {**result, "persona_replies": run_personas(request, ctl, run_id)}
    )


@router.get("/state", response_model=dict[str, Any])
def state(
    request: Request, run_id: str, principal: Inspector, at_sequence: int | None = None
) -> dict[str, Any]:
    """Canonical mailbox state. `at_sequence` (state as of that event) is evaluator-only."""
    view = inspector(request, principal)
    if at_sequence is None:
        return view.state(run_id)
    if not isinstance(view, EvaluatorView):
        raise PermissionDenied("at_sequence requires evaluator credentials")
    return view.state_at(run_id, at_sequence)


@router.get("/actions", response_model=list[s.ActionOut])
def actions(request: Request, run_id: str, principal: Inspector) -> list[s.ActionOut]:
    return [s.ActionOut.model_validate(a) for a in inspector(request, principal).actions(run_id)]


@router.get("/mailboxes", response_model=list[s.MailboxOut])
def list_mailboxes(request: Request, run_id: str, principal: Inspector) -> list[s.MailboxOut]:
    """Mailboxes of the run (operator view)."""
    return [s.MailboxOut(**mb) for mb in inspector(request, principal).list_mailboxes(run_id)]


@router.get("/mailboxes/{mailbox_id}/messages", response_model=s.MessagePage)
def inspect_messages(
    request: Request,
    run_id: str,
    mailbox_id: str,
    principal: Inspector,
    folder: str | None = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    order: Literal["asc", "desc"] = "asc",
) -> s.MessagePage:
    """Operator read of any mailbox in the run. Unlike the agent mailbox API this is not an
    agent action: nothing is recorded and no triggers are armed; finished runs are readable."""
    page = inspector(request, principal).inspect_messages(
        run_id, mailbox_id, folder=folder, page_size=page_size, cursor=cursor, order=order
    )
    return s.MessagePage(items=page.items, next_cursor=page.next_cursor)


@router.get("/mailboxes/{mailbox_id}/messages/{message_id}/attachments/{attachment_id}",
            response_class=Response, responses=ATTACHMENT_RESPONSE)  # fmt: skip
def inspect_attachment(
    request: Request, run_id: str, mailbox_id: str, message_id: str, attachment_id: str,
    principal: Inspector,
) -> Response:  # fmt: skip
    """Operator download of one attachment of any mailbox in the run (records nothing; works
    on finished runs). Always a download: `Content-Disposition: attachment`, `nosniff` and
    `Content-Security-Policy: sandbox`, so it is never rendered in the dashboard's origin."""
    ref, data = inspector(request, principal).inspect_attachment(run_id, mailbox_id, message_id,
                                                                 attachment_id)  # fmt: skip
    return attachment_response(ref, data)
