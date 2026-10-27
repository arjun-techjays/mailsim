"""SQLAlchemy 2.0 models.

Control-plane tables (tenants, projects, scenarios, runs) are keyed by prefixed ids.
Run-state ("projection") tables are keyed by (run_id, id): every mutable row belongs to
exactly one run, which is what gives per-run isolation and lets replay rebuild identical
state in a fresh run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


def _created() -> Mapped[datetime]:
    return mapped_column(TS, server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = _created()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("id", "tenant_id"),)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)  # e.g. finance.invoice-bank-change
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"]),
        UniqueConstraint("project_id", "key"),
    )


class ScenarioVersion(Base):
    """Immutable (enforced by DB trigger): editing a scenario creates a new version."""

    __tablename__ = "scenario_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String, nullable=False)
    fixtures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # path -> blob/sha
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_blob: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("scenario_id", "version"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    scenario_version_id: Mapped[str | None] = mapped_column(ForeignKey("scenario_versions.id"))
    status: Mapped[str] = mapped_column(String, nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    virtual_time: Mapped[datetime | None] = mapped_column(TS)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    id_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    agent_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # per-run resource caps (spec 10), current epoch: guarded agent reads/searches and
    # sent_copy commands (counted with their audit row), and events attributed to the agent
    # (counted as they are appended, so replay rebuilds it)
    agent_reads: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    agent_sent_copies: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    agent_events: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    start_time: Mapped[datetime | None] = mapped_column(TS)
    replay_of: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"]),
        Index("ix_runs_scope", "tenant_id", "project_id"),
        Index("ix_runs_scope_created", "tenant_id", "project_id", "created_at", "id"),
    )


class EventRow(Base):
    """Append-only event log (UPDATE/DELETE rejected by DB trigger)."""

    __tablename__ = "events"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False, default="ok")
    virtual_time: Mapped[datetime | None] = mapped_column(TS)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id_counter_after: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created()


class ActionRow(Base):
    """Audit trail of operations by agent or simulator, including denied ones."""

    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    virtual_time: Mapped[datetime | None] = mapped_column(TS)
    # Event-log window written by this command: sequences seq_start..seq_end (empty when
    # seq_end < seq_start). Lets evaluators place every action exactly in the log.
    seq_start: Mapped[int | None] = mapped_column(BigInteger)
    seq_end: Mapped[int | None] = mapped_column(BigInteger)
    # Interface the command came through (rest, gmail, graph, mcp_http, mcp_stdio, imap, smtp,
    # api, cli); None for engine-internal callers and rows written before migration 0009.
    interface: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_actions_run_id_id", "run_id", "id"),)  # activity polling


class AnswerKey(Base):
    """Hidden grading key, versioned independently of the scenario (immutable; DB trigger).

    Never referenced from runs, events or mailbox state: only the evaluator reads it.
    """

    __tablename__ = "answer_keys"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String, nullable=False)
    scenario_versions: Mapped[list[int] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String, nullable=False)  # file | inline
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("scenario_id", "version"),)


class Evaluation(Base):
    """One post-run evaluation of a run against one answer-key version. Repeatable."""

    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    answer_key_id: Mapped[str | None] = mapped_column(ForeignKey("answer_keys.id"))
    checks_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict: Mapped[str] = mapped_column(String, nullable=False)  # PASSED|FAILED|INCONCLUSIVE
    accuracy: Mapped[float | None] = mapped_column()
    agent_build: Mapped[str | None] = mapped_column(String)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class AssertionResult(Base):
    __tablename__ = "assertion_results"
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id"), primary_key=True)
    assertion_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()


class ReportRow(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id"), nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)  # json | junit | markdown
    blob_ref: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = _created()


class ApiKey(Base):
    """Sandbox-issued credential. Only a hash is stored. Agent keys bind run + mailbox."""

    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String)
    mailbox_id: Mapped[str | None] = mapped_column(String)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"]),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    # scoped to the credential: another principal can never replay someone else's response
    key_id: Mapped[str] = mapped_column(String, primary_key=True)
    method_path: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    created_at: Mapped[datetime] = _created()


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. 2026-10
    metric: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class LlmCall(Base):
    """Model usage metadata (outputs themselves are recorded in run events for replay)."""

    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)  # persona | judge | generation
    prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ok | failed: billed-but-failed calls (refusal, empty/malformed output) still count
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok", server_default="ok")
    usage_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cost_usd: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[datetime] = _created()


# ---- Run-state projection tables: PK (run_id, id) ------------------------------------


class _RunScoped:
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)


class Organization(_RunScoped, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    policies: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class Persona(_RunScoped, Base):
    __tablename__ = "personas"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    knowledge_scope: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    response_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "email"),)


class Relationship(_RunScoped, Base):
    __tablename__ = "relationships"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_persona: Mapped[str] = mapped_column(String, nullable=False)
    target_persona: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    trust_level: Mapped[str] = mapped_column(String, nullable=False)


class WorkProject(_RunScoped, Base):
    """The spec's domain `Project` (shared context), distinct from the tenancy project."""

    __tablename__ = "work_projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    participants: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    facts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class Mailbox(_RunScoped, Base):
    __tablename__ = "mailboxes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    provider_profile: Mapped[str] = mapped_column(String, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "email"),)


class Folder(_RunScoped, Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mailbox_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    system_type: Mapped[str | None] = mapped_column(String)
    uid_next: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    uid_validity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class Thread(_RunScoped, Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mailbox_id: Mapped[str] = mapped_column(String, nullable=False)
    subject_key: Mapped[str] = mapped_column(String, nullable=False)


class Message(_RunScoped, Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mailbox_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id_header: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    folder_id: Mapped[str] = mapped_column(String, nullable=False)
    previous_folder_id: Mapped[str | None] = mapped_column(String)
    imap_uid: Mapped[int | None] = mapped_column(BigInteger)
    from_: Mapped[dict[str, Any]] = mapped_column("from", JSONB, nullable=False)
    to: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    cc: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    bcc: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(String)
    references: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(TS)
    labels: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_blob_ref: Mapped[str | None] = mapped_column(String)
    provider_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        Index("ix_messages_mailbox", "run_id", "mailbox_id"),
        Index("ix_messages_header", "run_id", "mailbox_id", "message_id_header"),
    )


class Attachment(_RunScoped, Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    blob_ref: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_id: Mapped[str | None] = mapped_column(String)


class ScheduledEvent(_RunScoped, Base):
    __tablename__ = "scheduled_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    due_at: Mapped[datetime | None] = mapped_column(TS)  # NULL = armed by an agent action only
    order_key: Mapped[int] = mapped_column(Integer, nullable=False)  # declaration order
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending | armed | fired


class SinkMessage(_RunScoped, Base):
    """Sandbox-only store for external-recipient mail when delivery mode is `sink`."""

    __tablename__ = "sink_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    raw_blob_ref: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class ProviderId(_RunScoped, Base):
    """Provider-facing identifiers (spec 4.1: never interchangeable with internal ids).

    Filled by projectors, so replay reproduces them exactly.
    """

    __tablename__ = "provider_ids"
    provider: Mapped[str] = mapped_column(String, primary_key=True)  # gmail | graph
    entity_type: Mapped[str] = mapped_column(String, primary_key=True)  # message | thread
    external_id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (Index("ix_provider_ids_entity", "run_id", "entity_type", "entity_id"),)
