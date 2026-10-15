"""Simulator control (spec 7.2). Controller/evaluator credentials only. Owner: WS-A."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header

from mailsim.api import schemas as s
from mailsim.api.deps import Controller, Inspector, not_implemented
from mailsim.api.errors import ErrorEnvelope

router = APIRouter(prefix="/v1/runs/{run_id}", tags=["simulator"],
                   responses={"default": {"model": ErrorEnvelope}})  # fmt: skip
Idem = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


@router.post("/mailboxes/{mailbox_id}/inject", response_model=s.InjectResult, status_code=201)
def inject(
    run_id: str,
    mailbox_id: str,
    body: s.InjectRequest,
    principal: Controller,
    idempotency_key: Idem = None,
) -> s.InjectResult:
    raise not_implemented("WS-A")


@router.post("/clock/advance", response_model=s.ClockResult)
def advance(
    run_id: str, body: s.AdvanceRequest, principal: Controller, idempotency_key: Idem = None
) -> s.ClockResult:
    raise not_implemented("WS-A")


@router.post("/scheduler/tick", response_model=s.ClockResult)
def tick(run_id: str, principal: Controller) -> s.ClockResult:
    raise not_implemented("WS-A")


@router.get("/state", response_model=dict[str, Any])
def state(run_id: str, principal: Inspector, at_sequence: int | None = None) -> dict[str, Any]:
    raise not_implemented("WS-A")


@router.get("/actions", response_model=list[s.ActionOut])
def actions(run_id: str, principal: Inspector) -> list[s.ActionOut]:
    raise not_implemented("WS-A")
