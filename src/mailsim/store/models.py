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
    start_time: Mapped[datetime | None] = mapped_column(TS)
    replay_of: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"]),
        Index("ix_runs_scope", "tenant_id", "project_id"),
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
    created_at: Mapped[datetime] = _created()


class AssertionResult(Base):
    """Table only in Phase 1; the evaluator arrives in Phase 2."""

    __tablename__ = "assertion_results"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    assertion_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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
