"""Frozen REST request/response schemas for /v1 (Wave 0). Additive changes only."""

from __future__ import annotations

import math
from datetime import datetime
from email.utils import getaddresses
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from mailsim.domain.models import CanonicalMessage


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Request size caps (WS-A, additive): base64 of 5 MB per fixture / 25 MB raw MIME, 2 MB text.
MAX_SOURCE_CHARS = 2 * 1024 * 1024
MAX_FIXTURE_B64_CHARS = math.ceil(5 * 1024 * 1024 * 4 / 3) + 4
MAX_RAW_B64_CHARS = math.ceil(25 * 1024 * 1024 * 4 / 3) + 4
FixtureB64 = Annotated[str, Field(max_length=MAX_FIXTURE_B64_CHARS)]


# ---- control plane ---------------------------------------------------------------------
class ProjectCreate(_M):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(_M):
    id: str
    tenant_id: str
    name: str
    created_at: datetime


class ScenarioCreate(_M):
    """Scenario document (YAML text or parsed JSON) plus fixtures (path -> base64)."""

    source: str | None = Field(
        default=None, description="YAML or JSON text", max_length=MAX_SOURCE_CHARS
    )
    document: dict[str, Any] | None = None
    fixtures: dict[str, FixtureB64] = Field(
        default_factory=dict, description="relative path -> base64 bytes"
    )


class ScenarioVersionOut(_M):
    id: str
    scenario_key: str
    version: int
    spec_hash: str
    created_at: datetime


class ScenarioOut(_M):
    id: str
    key: str
    latest_version: ScenarioVersionOut | None
    versions: list[int]


class ValidationIssue(_M):
    path: str
    message: str


class ValidationResult(_M):
    ok: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]


class AnswerKeyCreate(_M):
    source: str | None = Field(default=None, max_length=MAX_SOURCE_CHARS)
    document: dict[str, Any] | None = None


class AnswerKeyOut(_M):
    id: str
    scenario_key: str
    version: int
    spec_hash: str
    scenario_versions: list[int] | None
    source: Literal["file", "inline"]
    created_at: datetime


class RunCreate(_M):
    scenario_version: int | None = Field(default=None, description="default: latest")
    checks: Literal["on", "off"] | None = Field(
        default=None, description="post-run deterministic checks override (hidden from agent)"
    )
    agent_build: str | None = Field(
        default=None, min_length=1, max_length=200,
        description="label for comparing builds in /accuracy; omit for none (stored as null, "
                    "reported as \"(no build)\", which is therefore not a valid name)",
    )  # fmt: skip
    start: bool = True


class RunOut(_M):
    id: str
    status: str = Field(description="DRAFT, VALIDATED, READY, RUNNING, PASSED, FAILED, ERROR, "
                                    "CANCELLED or REPLAYED (a closed replay copy)")  # fmt: skip
    epoch: int
    virtual_time: str | None
    agent_actions: int
    replay_of: str | None = Field(description="set on replay runs: the run whose recorded log "
                                              "this run copies (never evaluated)")  # fmt: skip
    scenario_key: str | None = None
    scenario_version: int | None = None
    agent_build: str | None = None


class AgentCredential(_M):
    """Returned once at run creation: the only credential the agent should receive."""

    api_key: str
    mailbox_id: str
    mailbox_address: str
    run_id: str
    key_id: str | None = Field(default=None, description="revoke with DELETE /v1/keys/{key_id}")


class RunCreated(_M):
    run: RunOut
    agent_credentials: list[AgentCredential]


class EventOut(_M):
    sequence: int
    epoch: int
    type: str
    actor: str
    operation: str
    outcome: str
    virtual_time: str | None
    payload: dict[str, Any]


class ActionOut(_M):
    actor: str
    operation: str
    input: dict[str, Any]
    result: dict[str, Any]
    outcome: str
    id: int | None = None
    virtual_time: str | None = None
    seq_start: int | None = Field(default=None, description="first event sequence it caused")
    interface: str | None = Field(default=None, description="interface the action came through")
    seq_end: int | None = Field(default=None, description="last event sequence it caused")


class ReplayOut(_M):
    replay_run_id: str
    matches: bool
    original_hash: str
    replay_hash: str
    event_count: int
    missing_blobs: list[str]


class ArtifactOut(_M):
    kind: Literal["raw_mime", "attachment", "report", "trace"]
    ref: str
    content_type: str
    size: int | None = None
    url: str | None = Field(default=None, description="download URL of a blob artifact "
                                                     "(served as an attachment)")  # fmt: skip


class EvaluationRequest(_M):
    answer_key_version: int | None = Field(default=None, description="default: latest compatible")
    checks: Literal["on", "off"] | None = None
    complete_run: bool = Field(default=True, description="set PASSED/FAILED on a RUNNING run")


class PageOut(_M):
    next_cursor: str | None = None


class EventPage(PageOut):
    items: list[EventOut]


class MessagePage(PageOut):
    items: list[CanonicalMessage]


# ---- simulator control -----------------------------------------------------------------
class InjectMessage(_M):
    from_: str = Field(alias="from")
    to: list[str] = Field(min_length=1)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    text: str | None = None
    html: str | None = None
    in_reply_to: str | None = None
    references: list[str] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InjectRequest(_M):
    event_id: str | None = None
    message: InjectMessage | None = None
    raw_base64: str | None = Field(default=None, max_length=MAX_RAW_B64_CHARS)
    delivery: dict[str, Any] = Field(default_factory=lambda: {"mode": "immediate"})


class InjectResult(_M):
    event_id: str
    message_id: str
    delivery_status: Literal["delivered_to_simulated_mailbox"]


class AdvanceRequest(_M):
    seconds: float = Field(ge=0)


class ClockResult(_M):
    fired: list[str]
    virtual_time: str
    persona_replies: int = Field(
        default=0, description="persona messages delivered by the persona runtime in this step"
    )


# ---- agent mailbox ---------------------------------------------------------------------
def normalise_addresses(values: list[str]) -> list[str]:
    """Recipient entries -> bare addresses. Each entry may be `addr@x`, `Name <addr@x>`,
    `"Last, First" <addr@x>` or several of those separated by commas. An entry that holds no
    parseable address is kept verbatim so the engine rejects it as INVALID_REQUEST."""
    out: list[str] = []
    for value in values:
        found = [addr.strip() for _name, addr in getaddresses([value]) if addr.strip()]
        out += found or [value]
    return out


MAX_RECIPIENTS = 100  # per list before "a, b" expansion; the engine caps the distinct total
Recipients = Annotated[
    list[Annotated[str, Field(max_length=8192)]],
    AfterValidator(normalise_addresses),
    Field(
        max_length=MAX_RECIPIENTS,
        description='addresses; "Name <addr@example.com>" is accepted (the name is dropped); '
        "quote a name that contains a comma ('\"Smith, John\" <j@example.com>'): unquoted "
        "commas separate addresses. At most 100 distinct recipients per message.",
    ),
]


class SendRequest(_M):
    to: Recipients = Field(default_factory=list)
    cc: Recipients = Field(default_factory=list)
    bcc: Recipients = Field(default_factory=list)
    subject: str = ""
    text: str = ""
    html: str | None = None


class ReplyRequest(_M):
    text: str
    reply_all: bool = False


class ForwardRequest(_M):
    """Forward a message (with its attachments) to new recipients; `text` goes above it."""

    to: Recipients = Field(min_length=1)
    cc: Recipients = Field(default_factory=list)
    text: str = ""


MAX_PATCH_LABELS = 50
LabelName = Annotated[str, Field(max_length=200)]


class MessagePatch(_M):
    folder: str | None = None
    add_labels: list[LabelName] = Field(default_factory=list, max_length=MAX_PATCH_LABELS)
    remove_labels: list[LabelName] = Field(default_factory=list, max_length=MAX_PATCH_LABELS)
    add_flags: list[str] = Field(default_factory=list)
    remove_flags: list[str] = Field(default_factory=list)


class SendResult(_M):
    """The sent copy in the caller's mailbox. How each recipient was handled is not part of
    the agent-facing result (operators see it in the run's actions and events)."""

    message_id: str
    message_id_header: str


class FolderOut(_M):
    id: str
    name: str
    system_type: str | None


class FolderCountOut(FolderOut):
    total: int = 0
    unread: int = Field(default=0, description="messages without the SEEN flag")


class MailboxOut(_M):
    id: str
    owner_id: str
    email: str
    persona_id: str | None = None
    name: str | None = Field(default=None, description="persona display name")
    role: str | None = None
    is_agent: bool = False
    folders: list[FolderCountOut] = Field(default_factory=list)


# ---- additive (Wave 1, WS-A) -------------------------------------------------------------
class IssuedCredential(_M):
    """A newly issued sandbox API key. `api_key` is shown once and never stored in clear."""

    api_key: str
    key_id: str
    role: Literal["controller", "evaluator", "agent"]


class ProjectCreated(ProjectOut):
    """`POST /v1/projects` response: the project plus its first controller/evaluator keys."""

    credentials: list[IssuedCredential]


class ChangeOut(_M):
    sequence: int
    type: str
    message_id: str | None


class ChangeFeed(_M):
    """Agent mailbox change feed keyed by event sequence (resume from `next_since`)."""

    changes: list[ChangeOut]
    next_since: int
    has_more: bool
    latest_sequence: int
    reset_since: bool


class RunPage(PageOut):
    items: list[RunOut]


class HealthOut(_M):
    """`GET /v1/health` (no credentials needed)."""

    status: Literal["ok"]
    version: str
    local_no_auth: bool = Field(
        default=False, description="true when the server runs in local no-auth mode"
    )


class MeOut(_M):
    """The calling credential's identity (`GET /v1/me`)."""

    role: Literal["controller", "evaluator", "agent", "operator"]
    tenant_id: str
    project_id: str
    run_id: str | None
    mailbox_id: str | None
    key_id: str | None
    local_no_auth: bool = Field(
        default=False, description="true for the key-less local operator (loopback dev mode)"
    )
