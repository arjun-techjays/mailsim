"""UX contract (dashboard setup and connection flows). Additive, operator-side only.

None of these endpoints is reachable with an agent key, and none returns answer-key content
except the evaluator-only answer-key document endpoints.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mailsim.api.schemas import (
    _M,
    MAX_FIXTURE_B64_CHARS,
    MAX_SOURCE_CHARS,
    FixtureB64,
    ValidationIssue,
)

Interface = Literal["rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp"]
INTERFACES: tuple[str, ...] = ("rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp")


# ---- meta --------------------------------------------------------------------------------
class AssertionTypeInfo(_M):
    type: str
    default_category: str
    check_schema: dict[str, Any] | None = Field(
        description="JSON Schema of this type's `check:` block (None: rubric only)"
    )


class AuthoringMeta(_M):
    """Everything a form-based editor needs to offer choices instead of free text."""

    scenario_schema: dict[str, Any] = Field(description="JSON Schema of a scenario document")
    answer_key_schema: dict[str, Any] = Field(description="JSON Schema of an answer key")
    schema_versions: list[str]
    agent_operations: list[str]
    trigger_types: list[str]
    trigger_actions: list[str] = Field(description="values for after_agent_action triggers")
    external_delivery_modes: list[str]
    behavior_modes: list[str]
    assertion_types: list[AssertionTypeInfo]
    severities: list[str]
    categories: list[str]
    invariants: list[str]
    folders: list[str] = Field(description="system folder types usable in checks")
    flags: list[str]


class ListenerStatus(_M):
    running: bool
    host: str | None = None
    port: int | None = None
    tls: Literal["none", "starttls"] = "none"


class SystemStatus(_M):
    version: str
    local_no_auth: bool
    llm_provider: str
    llm_model: str | None = None
    interfaces: list[Interface] = Field(description="interfaces this server serves")
    listeners: dict[Literal["imap", "smtp"], ListenerStatus]


# ---- YAML tools ----------------------------------------------------------------------------
class YamlToJson(_M):
    source: str = Field(max_length=MAX_SOURCE_CHARS)


class JsonToYaml(_M):
    document: dict[str, Any]


class ParsedDocument(_M):
    """`document` is None when the text does not parse; `error` then says where."""

    document: dict[str, Any] | None = None
    error: ValidationIssue | None = None


class RenderedYaml(_M):
    source: str


# ---- scenario drafts ----------------------------------------------------------------------
class ScenarioDraft(_M):
    """A scenario being edited: YAML/JSON text OR a document, plus fixtures (path -> base64)."""

    source: str | None = Field(default=None, max_length=MAX_SOURCE_CHARS)
    document: dict[str, Any] | None = None
    fixtures: dict[str, FixtureB64] = Field(default_factory=dict)


class DraftValidation(_M):
    """Live validation: never stores anything. `document` is the parsed document (if any)."""

    ok: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    document: dict[str, Any] | None = None


class ComposeAttachment(_M):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=255)
    content_b64: str = Field(max_length=MAX_FIXTURE_B64_CHARS)


class ComposeMessage(_M):
    """A message composed in a form, built into an RFC 5322 .eml fixture by the server."""

    from_: str = Field(alias="from")
    to: list[str] = Field(min_length=1, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=998)
    text: str | None = None
    html: str | None = None
    date: str | None = Field(
        default=None,
        description="ISO 8601; default: current UTC time; clients should send the scenario clock start",
    )
    message_id: str | None = Field(default=None, description="default: generated <...@domain>")
    in_reply_to: str | None = Field(default=None, description="Message-ID of an earlier message")
    references: list[str] = Field(default_factory=list)
    attachments: list[ComposeAttachment] = Field(default_factory=list, max_length=20)
    fixture_path: str | None = Field(
        default=None, description="default: fixtures/<subject slug>-<id>.eml"
    )


class ComposedFixture(_M):
    path: str
    content_b64: str
    message_id: str
    size: int


class EmlSummary(_M):
    """What an uploaded .eml contains (to show it in the form)."""

    message_id: str | None
    from_: str | None = Field(alias="from")
    to: list[str]
    cc: list[str]
    subject: str
    date: str | None
    in_reply_to: str | None
    references: list[str] = Field(default_factory=list, description="Message-IDs, oldest first")
    attachments: list[str]
    text_preview: str


class EmlInspect(_M):
    content_b64: str = Field(max_length=MAX_FIXTURE_B64_CHARS)


class ScenarioVersionDocument(_M):
    """A published version for editing/cloning: the stored (agent-facing) document, which never
    contains answer-key assertions, plus its fixtures."""

    scenario_key: str
    version: int
    spec_hash: str
    document: dict[str, Any]
    fixtures: dict[str, str] = Field(description="relative path -> base64 bytes")


# ---- answer keys --------------------------------------------------------------------------
class AnswerKeyDraft(_M):
    source: str | None = Field(default=None, max_length=MAX_SOURCE_CHARS)
    document: dict[str, Any] | None = None


class AnswerKeyValidation(_M):
    """Schema errors (with paths) and leak-guard findings (path "leak:<scenario version>")."""

    ok: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    document: dict[str, Any] | None = None


class AnswerKeyDocument(_M):
    """Evaluator-only: the full answer key (assertions included)."""

    scenario_key: str
    version: int
    spec_hash: str
    document: dict[str, Any]


# ---- connecting an agent ------------------------------------------------------------------
class KeyInfo(_M):
    key_id: str
    prefix: str = Field(description="the <prefix> of msk_<prefix>_... (not secret)")
    created_at: str
    expires_at: str | None = None
    revoked: bool = False


class InterfaceEndpoint(_M):
    interface: Interface
    available: bool = Field(description="served by this server / listener running")
    base_url: str | None = Field(
        default=None,
        description="service base URL for HTTP interfaces: <origin>/v1, <origin>/gmail/v1, "
        "<origin>/graph/v1.0, <origin>/mcp",
    )
    host: str | None = None
    port: int | None = None
    tls: Literal["none", "starttls"] | None = None
    username: str | None = Field(default=None, description="IMAP/SMTP login (mailbox address)")
    command: list[str] | None = Field(default=None, description="mcp_stdio argv")
    note: str | None = None


class RunConnection(_M):
    """How an agent connects to this run. Never contains a key (keys are shown once)."""

    run_id: str
    run_status: str
    mailbox_id: str
    mailbox_address: str
    persona_id: str
    interfaces: list[InterfaceEndpoint]
    agent_keys: list[KeyInfo] = Field(description="this run's agent keys (no secrets)")


class ConnectionTestRequest(_M):
    interface: Interface
    api_key: str = Field(min_length=1, max_length=200)


class ConnectionTestResult(_M):
    """Always HTTP 200. A key that is not a live agent key of the run gives ok=false with
    error_code UNAUTHENTICATED (details.reason invalid_key) or PERMISSION_DENIED (not_agent_key,
    other_run). Listener not running: LISTENER_DOWN. The key is never echoed."""

    interface: Interface
    ok: bool
    summary: str = Field(description="human-readable outcome")
    status_code: int | None = Field(default=None, description="HTTP/IMAP/SMTP status if any")
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---- activity -----------------------------------------------------------------------------
class ActivityItem(_M):
    id: int
    actor: str
    operation: str
    outcome: str
    interface: str | None = Field(
        description="rest|gmail|graph|mcp_http|mcp_stdio|imap|smtp|"
        "dashboard|cli, or None for older rows"
    )
    virtual_time: str | None
    input: dict[str, Any]
    result: dict[str, Any]


class ActivityPage(_M):
    items: list[ActivityItem]
    next_after_id: int | None = Field(description="poll again with after_id=<this>")
