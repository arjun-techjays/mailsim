"""Pydantic model of the scenario spec (spec section 6, schema_version "1.0").

YAML is the authoring format; the canonical validated representation is the JSON produced
by `canonical_json`. Unknown keys are rejected so typos surface as validation errors.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mailsim.scenario.answer_key import ASSERTION_TYPES as ASSERTION_TYPES
from mailsim.scenario.answer_key import AnswerAssertion

SCHEMA_VERSION = "1.1"
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")
AGENT_OPERATIONS = (
    "read", "search", "reply", "send", "forward", "insert", "move", "copy", "label", "flag",
    "draft", "delete",
)  # fmt: skip
TRIGGER_ACTIONS = {
    "send_reply": "reply",
    "reply": "reply",
    "send_message": "send",
    "send": "send",
    "read": "read",
    "search": "search",
    "move": "move",
    "label": "label",
    "flag": "flag",
    "draft": "draft",
    "delete": "delete",
    "forward": "forward",
    "insert": "insert",
    "copy": "copy",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClockSpec(_Strict):
    start_at: str
    mode: Literal["virtual"] = "virtual"


class DeliverySpec(_Strict):
    external_delivery: Literal["deny", "sink", "event"] = "deny"
    persona_replies: Literal["enabled", "disabled"] = "enabled"

    @field_validator("external_delivery", mode="before")
    @classmethod
    def _never_allow(cls, v: Any) -> Any:
        if v in ("allow", "allowed", "enabled", True):
            raise ValueError("external delivery can never be enabled: use deny, sink or event")
        return v


class EnvironmentSpec(_Strict):
    seed: Annotated[int, Field(ge=0)]
    clock: ClockSpec
    delivery: DeliverySpec = Field(default_factory=DeliverySpec)


class PolicySpec(_Strict):
    id: str
    rule: str


class OrganizationSpec(_Strict):
    domain: str
    name: str | None = None
    policies: list[PolicySpec] = Field(default_factory=list)


class ScriptMatch(_Strict):
    from_: str | None = Field(default=None, alias="from")  # persona id
    subject_contains: str | None = None
    body_contains: str | None = None


class ScriptReply(_Strict):
    subject: str | None = None  # default "Re: <subject>"
    body: str


class ScriptRule(_Strict):
    """Scripted persona policy (1.1): deterministic reply when an incoming mail matches."""

    on: ScriptMatch = Field(default_factory=ScriptMatch)
    reply: ScriptReply
    delay_seconds: Annotated[int, Field(ge=0)] | None = None  # default: response_delay_seconds
    once: bool = True


class BehaviorSpec(_Strict):
    response_style: str | None = None
    response_delay_seconds: Annotated[int, Field(ge=0)] = 0
    mode: Literal["silent", "scripted", "llm"] = "silent"  # 1.1; LLM output is recorded
    script: list[ScriptRule] = Field(default_factory=list)


class PersonaSpec(_Strict):
    id: str
    name: str | None = None  # display name (cosmetic; used in From/To display names)
    email: str
    role: str
    knowledge: list[str] = Field(default_factory=list)
    behavior: BehaviorSpec = Field(default_factory=BehaviorSpec)


class RelationshipSpec(_Strict):
    source: str
    target: str
    type: str
    trust_level: Literal["low", "medium", "high"] = "medium"


class WorkProjectSpec(_Strict):
    id: str
    name: str
    participants: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)


class InitialMessageSpec(_Strict):
    fixture: str


class MailboxSpec(_Strict):
    owner: str
    initial_messages: list[InitialMessageSpec] = Field(default_factory=list)


class TriggerSpec(_Strict):
    type: Literal["at", "after_seconds", "after_agent_action"]
    at: str | None = None
    seconds: Annotated[int, Field(ge=0)] | None = None
    action: str | None = None
    delay_seconds: Annotated[int, Field(ge=0)] = 0


class ReceiveEventSpec(_Strict):
    type: Literal["email.receive"]
    from_: str = Field(alias="from")
    to: str | list[str]
    subject: str
    body: str


class EventSpec(_Strict):
    id: str
    trigger: TriggerSpec
    event: ReceiveEventSpec


class AgentSpec(_Strict):
    mailbox: str
    allowed_operations: list[str] = Field(default_factory=list)
    prohibited_operations: list[str] = Field(default_factory=list)


class LimitsSpec(_Strict):
    max_agent_actions: Annotated[int, Field(gt=0)] = 100
    max_virtual_duration_seconds: Annotated[int, Field(gt=0)] = 86400


class EvaluationSpec(_Strict):
    # Inline assertions are an authoring convenience only: on publish they are moved into a
    # separately versioned answer key and stripped from the stored scenario (never visible
    # to the agent). Prefer a standalone answer-key file.
    assertions: list[AnswerAssertion] = Field(default_factory=list)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)


class ScenarioBody(_Strict):
    id: str
    version: Annotated[int, Field(ge=1)]
    title: str
    tags: list[str] = Field(default_factory=list)
    environment: EnvironmentSpec
    organization: OrganizationSpec
    personas: list[PersonaSpec] = Field(default_factory=list)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    projects: list[WorkProjectSpec] = Field(default_factory=list)
    mailboxes: list[MailboxSpec] = Field(default_factory=list)
    events: list[EventSpec] = Field(default_factory=list)
    agent: AgentSpec
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)


class ScenarioDoc(_Strict):
    schema_version: Literal["1.0", "1.1"]
    scenario: ScenarioBody


def strip_answer_key(spec: dict[str, Any]) -> dict[str, Any]:
    """The stored/run-facing scenario: identical minus inline assertions."""
    out: dict[str, Any] = json.loads(json.dumps(spec))
    out["scenario"]["evaluation"]["assertions"] = []
    return out


def canonical_dict(doc: ScenarioDoc) -> dict[str, Any]:
    return doc.model_dump(mode="json", by_alias=True)


def canonical_json(doc: ScenarioDoc | dict[str, Any]) -> str:
    data = canonical_dict(doc) if isinstance(doc, ScenarioDoc) else doc
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_hash(doc: ScenarioDoc | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()


def json_schema() -> dict[str, Any]:
    """Exported JSON Schema, usable for editor integration and CI."""
    return ScenarioDoc.model_json_schema(by_alias=True)
