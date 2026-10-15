"""Dashboard setup and connection flows (UX story). Operator-side only: every route requires a
controller and/or evaluator key (or the local no-auth operator); none is reachable with an
agent key. Contract owner: integrator; implementation: UX-B.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from mailsim.api import ux_schemas as u
from mailsim.api.deps import Controller, Evaluator, Inspector
from mailsim.api.errors import ErrorEnvelope
from mailsim.domain.errors import UnsupportedOperation

router = APIRouter(
    prefix="/v1", tags=["authoring"], responses={"default": {"model": ErrorEnvelope}}
)


def _todo() -> UnsupportedOperation:  # pragma: no cover - replaced by UX-B
    return UnsupportedOperation("not implemented yet (UX contract stub)")


@router.get("/meta/authoring", response_model=u.AuthoringMeta)
def authoring_meta(principal: Inspector) -> u.AuthoringMeta:
    """Schemas and enumerations for the scenario builder and the answer-key editor."""
    raise _todo()


@router.get("/system/status", response_model=u.SystemStatus)
def system_status(request: Request, principal: Inspector) -> u.SystemStatus:
    """Server mode, LLM provider and which interfaces/listeners are up."""
    raise _todo()


@router.post("/tools/yaml-to-json", response_model=u.ParsedDocument)
def yaml_to_json(body: u.YamlToJson, principal: Inspector) -> u.ParsedDocument:
    """Parse YAML/JSON text (safe loader, same limits as publishing)."""
    raise _todo()


@router.post("/tools/json-to-yaml", response_model=u.RenderedYaml)
def json_to_yaml(body: u.JsonToYaml, principal: Inspector) -> u.RenderedYaml:
    """Render a document as YAML (stable key order: the form's order)."""
    raise _todo()


@router.post("/scenario-drafts/validate", response_model=u.DraftValidation)
def validate_draft(body: u.ScenarioDraft, principal: Controller) -> u.DraftValidation:
    """Validate an unsaved scenario (schema, references, fixtures). Stores nothing."""
    raise _todo()


@router.post("/scenario-drafts/compose-eml", response_model=u.ComposedFixture)
def compose_eml(body: u.ComposeMessage, principal: Controller) -> u.ComposedFixture:
    """Build an .eml fixture from a composed message (threading headers, attachments)."""
    raise _todo()


@router.post("/scenario-drafts/inspect-eml", response_model=u.EmlSummary)
def inspect_eml(body: u.EmlInspect, principal: Controller) -> u.EmlSummary:
    """Summarise an uploaded .eml for display in the form."""
    raise _todo()


@router.get("/scenarios/{scenario_key}/versions/{version}/document",
            response_model=u.ScenarioVersionDocument)  # fmt: skip
def scenario_version_document(
    request: Request, scenario_key: str, version: int, principal: Controller
) -> u.ScenarioVersionDocument:
    """A published version's (agent-facing) document and fixtures, for editing or cloning."""
    raise _todo()


@router.post("/scenarios/{scenario_key}/answer-keys/validate",
             response_model=u.AnswerKeyValidation)  # fmt: skip
def validate_answer_key(
    request: Request, scenario_key: str, body: u.AnswerKeyDraft, principal: Inspector
) -> u.AnswerKeyValidation:
    """Schema + leak guard for an unsaved answer key. Stores nothing."""
    raise _todo()


@router.get("/scenarios/{scenario_key}/answer-keys/{version}/document",
            response_model=u.AnswerKeyDocument)  # fmt: skip
def answer_key_document(
    request: Request, scenario_key: str, version: int, principal: Evaluator
) -> u.AnswerKeyDocument:
    """Evaluator-only: the full answer key, for editing."""
    raise _todo()


@router.get("/runs/{run_id}/connection", response_model=u.RunConnection)
def run_connection(request: Request, run_id: str, principal: Controller) -> u.RunConnection:
    """How an agent connects to this run (endpoints, mailbox, key ids; never key secrets)."""
    raise _todo()


@router.post("/runs/{run_id}/connection-tests", response_model=u.ConnectionTestResult)
def connection_test(
    request: Request, run_id: str, body: u.ConnectionTestRequest, principal: Controller
) -> u.ConnectionTestResult:
    """Make a real call through the chosen interface with the given agent key, server-side."""
    raise _todo()


@router.get("/runs/{run_id}/activity", response_model=u.ActivityPage)
def run_activity(
    request: Request, run_id: str, principal: Inspector,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> u.ActivityPage:  # fmt: skip
    """Recorded actions (newest last) with the interface each came through, for polling."""
    raise _todo()
