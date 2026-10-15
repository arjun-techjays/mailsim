"""Control plane (spec 7.1) + answer keys + evaluations. Owner: WS-A (evaluations: WS-B)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from mailsim.api import schemas as s
from mailsim.api.deps import get_principal, not_implemented
from mailsim.api.errors import ErrorEnvelope
from mailsim.auth.principal import Principal
from mailsim.evaluation.contract import BuildComparison, EvaluationReport

router = APIRouter(prefix="/v1", tags=["control"], responses={"default": {"model": ErrorEnvelope}})
P = Annotated[Principal, Depends(get_principal)]
Idem = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


@router.post("/projects", response_model=s.ProjectOut, status_code=201)
def create_project(body: s.ProjectCreate, principal: P) -> s.ProjectOut:
    raise not_implemented("WS-A")


@router.get("/projects/{project_id}", response_model=s.ProjectOut)
def get_project(project_id: str, principal: P) -> s.ProjectOut:
    raise not_implemented("WS-A")


@router.post("/scenarios", response_model=s.ScenarioVersionOut, status_code=201)
def create_scenario(
    body: s.ScenarioCreate, principal: P, idempotency_key: Idem = None
) -> s.ScenarioVersionOut:
    raise not_implemented("WS-A")


@router.get("/scenarios", response_model=list[s.ScenarioOut])
def list_scenarios(
    principal: P, tag: str | None = None, q: str | None = None
) -> list[s.ScenarioOut]:
    raise not_implemented("WS-A")


@router.get("/scenarios/{scenario_key}", response_model=s.ScenarioOut)
def get_scenario(scenario_key: str, principal: P) -> s.ScenarioOut:
    raise not_implemented("WS-A")


@router.post("/scenarios/{scenario_key}/validate", response_model=s.ValidationResult)
def validate_scenario(
    scenario_key: str, body: s.ScenarioCreate, principal: P
) -> s.ValidationResult:
    raise not_implemented("WS-A")


@router.post(
    "/scenarios/{scenario_key}/answer-keys", response_model=s.AnswerKeyOut, status_code=201
)
def create_answer_key(scenario_key: str, body: s.AnswerKeyCreate, principal: P) -> s.AnswerKeyOut:
    raise not_implemented("WS-A")


@router.get("/scenarios/{scenario_key}/answer-keys", response_model=list[s.AnswerKeyOut])
def list_answer_keys(scenario_key: str, principal: P) -> list[s.AnswerKeyOut]:
    raise not_implemented("WS-A")


@router.post("/scenarios/{scenario_key}/runs", response_model=s.RunCreated, status_code=201)
def create_run(
    scenario_key: str, body: s.RunCreate, principal: P, idempotency_key: Idem = None
) -> s.RunCreated:
    raise not_implemented("WS-A")


@router.get("/scenarios/{scenario_key}/accuracy", response_model=BuildComparison)
def scenario_accuracy(
    scenario_key: str,
    principal: P,
    answer_key_version: int | None = None,
    builds: Annotated[list[str] | None, Query()] = None,
    baseline: str | None = None,
) -> BuildComparison:
    raise not_implemented("WS-B")


@router.get("/runs/{run_id}", response_model=s.RunOut)
def get_run(run_id: str, principal: P) -> s.RunOut:
    raise not_implemented("WS-A")


@router.post("/runs/{run_id}/start", response_model=s.RunOut)
def start_run(run_id: str, principal: P) -> s.RunOut:
    raise not_implemented("WS-A")


@router.post("/runs/{run_id}/cancel", response_model=s.RunOut)
def cancel_run(run_id: str, principal: P, idempotency_key: Idem = None) -> s.RunOut:
    raise not_implemented("WS-A")


@router.post("/runs/{run_id}/reset", response_model=s.RunOut)
def reset_run(run_id: str, principal: P, idempotency_key: Idem = None) -> s.RunOut:
    raise not_implemented("WS-A")


@router.get("/runs/{run_id}/events", response_model=s.EventPage)
def run_events(run_id: str, principal: P, cursor: str | None = None,
               page_size: Annotated[int, Query(ge=1, le=500)] = 100) -> s.EventPage:  # fmt: skip
    raise not_implemented("WS-A")


@router.post("/runs/{run_id}/replay", response_model=s.ReplayOut)
def replay_run(run_id: str, principal: P, idempotency_key: Idem = None) -> s.ReplayOut:
    raise not_implemented("WS-A")


@router.get("/runs/{run_id}/artifacts", response_model=list[s.ArtifactOut])
def run_artifacts(run_id: str, principal: P) -> list[s.ArtifactOut]:
    raise not_implemented("WS-A")


@router.post("/runs/{run_id}/evaluations", response_model=EvaluationReport, status_code=201)
def evaluate_run(
    run_id: str, body: s.EvaluationRequest, principal: P, idempotency_key: Idem = None
) -> EvaluationReport:
    raise not_implemented("WS-B")


@router.get("/runs/{run_id}/evaluations", response_model=list[EvaluationReport])
def list_evaluations(run_id: str, principal: P) -> list[EvaluationReport]:
    raise not_implemented("WS-B")


@router.get("/runs/{run_id}/results", response_model=EvaluationReport)
def run_results(run_id: str, principal: P) -> EvaluationReport:
    """Latest evaluation of the run (spec 7.1 /results)."""
    raise not_implemented("WS-B")
