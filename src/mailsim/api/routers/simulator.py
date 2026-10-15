"""Simulator control (spec 7.2). Controller/evaluator credentials only. Owner: WS-A."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response

from mailsim.api import schemas as s
from mailsim.api.deps import Controller, Inspector
from mailsim.api.errors import ErrorEnvelope
from mailsim.api.idempotency import idempotent
from mailsim.api.routers._common import controller, inspector
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
        return s.ClockResult.model_validate(
            controller(request, principal).advance(run_id, body.seconds)
        )

    return idempotent(request, principal, idempotency_key, body.model_dump(mode="json"), 200, run)


@router.post("/scheduler/tick", response_model=s.ClockResult)
def tick(request: Request, run_id: str, principal: Controller) -> s.ClockResult:
    return s.ClockResult.model_validate(controller(request, principal).tick(run_id))


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
