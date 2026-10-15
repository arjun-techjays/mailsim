"""Pydantic model of the scenario spec (spec section 6, schema_version "1.0").

YAML is the authoring format; the canonical validated representation is the JSON produced
by `canonical_json`. Unknown keys are rejected so typos surface as validation errors.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"
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
ASSERTION_TYPES = (
    "state", "action", "content", "policy", "safety", "temporal", "protocol", "semantic",
    "invariant",
)  # fmt: skip


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


class BehaviorSpec(_Strict):
    response_style: str | None = None
    response_delay_seconds: Annotated[int, Field(ge=0)] = 0


class PersonaSpec(_Strict):
    id: str
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


class AssertionSpec(_Strict):
    id: str
    type: str
    requirement: str


class LimitsSpec(_Strict):
    max_agent_actions: Annotated[int, Field(gt=0)] = 100
    max_virtual_duration_seconds: Annotated[int, Field(gt=0)] = 86400


class EvaluationSpec(_Strict):
    assertions: list[AssertionSpec] = Field(default_factory=list)
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
    schema_version: Literal["1.0"]
    scenario: ScenarioBody


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
