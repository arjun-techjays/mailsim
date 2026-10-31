"""SimEngine: the canonical command surface over the event log.

Every public method is a command executed in one transaction under the run lock:
guard (run active, permission, limits) -> mutate via events -> record an audit Action.
Denied and failed commands are recorded as Actions too (in their own transaction).
Provider adapters (later phases) translate their native semantics into these commands.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import any_, func, literal, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import (
    Conflict,
    ExternalDeliveryBlocked,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    RunNotActive,
    SimError,
    SimulationLimitExceeded,
    UnsupportedOperation,
)
from mailsim.domain.ids import IdGenerator, new_control_id
from mailsim.domain.models import (
    SYSTEM_FOLDERS,
    VALID_FLAGS,
    Address,
    AttachmentRef,
    CanonicalMessage,
    draft_flag_error,
    is_self_addressed,
    normalize_address,
    refuse_system_labels,
)
from mailsim.domain.states import NO_BUILD, TERMINAL, RunStatus, check_transition
from mailsim.engine import projectors as _projectors  # noqa: F401  (registers projectors)
from mailsim.engine.context import (
    connection_test,
    current_interface,
    is_read_back,
    is_read_paid,
    note_agent_read,
    note_agent_write,
)
from mailsim.engine.delivery import DeliveryPolicy, ExternalMode
from mailsim.engine.log import Event, append, id_generator, lock_run, save_ids
from mailsim.engine.pagination import Page, check_page_size, decode_cursor, encode_cursor
from mailsim.engine.scheduler import Scheduled, due_in_order
from mailsim.engine.snapshot import snapshot, state_hash
from mailsim.mime.build import build_mime
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedAttachment, parse_mime
from mailsim.scenario.answer_keys import answer_key_for
from mailsim.scenario.schema import (
    DEFAULT_RUN_CAPS,
    TRIGGER_ACTIONS,
    UNGATED_AGENT_OPERATIONS,
    ScenarioBody,
    ScenarioDoc,
)
from mailsim.scenario.service import get_scenario_version
from mailsim.scenario.validator import validate_semantics
from mailsim.store import models as m
from mailsim.store.blob import BlobStore
from mailsim.store.repos import Scope, get_project, get_run
from mailsim.threads.assign import ThreadCandidate, decide_thread, subject_key

T = TypeVar("T")
AGENT = "agent"
CONTROLLER = "controller"
SIMULATOR = "simulator"
EVALUATOR = "evaluator"
# Recorded actor of an agent-key read made by the operator's connection test (never graded).
CONNECTION_TEST = "connection_test"
# Budget rule (user decision): only WRITES consume the agent action budget. Reads and polling
# are free but still recorded as actions (efficiency metrics).
READ_OPERATIONS = frozenset({"read", "search"})
PRESENT_CHUNK = 500  # ids per IN list in present_messages
MAX_ADVANCE_SECONDS = 10 * 365 * 86400  # longest single clock advance
# Upper bound of one attachment read (interfaces may pass a lower one, e.g. MCP's base64 cap).
MAX_ATTACHMENT_READ_BYTES = 25 * 1024 * 1024
# Distinct recipients (to + cc + bcc) of one agent send, whatever the interface (SMTP's
# RCPT limit is the same).
MAX_RECIPIENTS = 100


def counts_against_budget(operation: str) -> bool:
    return operation not in READ_OPERATIONS and operation not in UNGATED_AGENT_OPERATIONS


def run_cap(config: dict[str, Any], name: str) -> int:
    """The run's value of a per-run resource cap (scenario `evaluation.limits`); runs whose
    scenario does not set it, including runs created before the cap existed, get the
    server default."""
    value = (config.get("limits") or {}).get(name)
    return int(value) if value is not None else DEFAULT_RUN_CAPS[name]


def labels_per_message_exceeded(limit: int) -> SimulationLimitExceeded:
    """The refusal for a message that would carry more agent labels than max_labels_per_message."""
    return _limit_exceeded("labels per message", "max_labels_per_message", limit)


def _limit_exceeded(what: str, cap: str, limit: int) -> SimulationLimitExceeded:
    """The refusal for a reached cap. `details.cap` names it for operators and the evaluator
    (`no_limit_exceeded` with `limit:`); an agent sees neither (api.agent_errors)."""
    return SimulationLimitExceeded(f"{what} limit of {limit} reached",
                                   details={"limit": limit, "cap": cap})  # fmt: skip


@dataclass
class Ctx:
    session: Session
    run: m.Run
    gen: IdGenerator
    _names: dict[str, str] | None = None
    # further operations this command also performed, for `after_agent_action` triggers (an
    # insert carrying flags/labels fires flag/label triggers as the separate writes did)
    also_triggers: list[str] = field(default_factory=list)

    def persona_names(self) -> dict[str, str]:
        """email -> display name of the scenario's personas that have one (PersonaSpec.name).
        Cosmetic: used for MIME From/To/Cc only; matching always uses the address."""
        if self._names is None:
            sv = self.session.get(m.ScenarioVersion, self.run.scenario_version_id)
            personas = ((sv.spec.get("scenario") or {}).get("personas") or []) if sv else []
            self._names = {str(p["email"]).lower(): str(p["name"]) for p in personas
                           if p.get("name") and p.get("email")}  # fmt: skip
        return self._names

    def named(self, addr: Address) -> Address:
        name = addr.name or self.persona_names().get(addr.email.lower())
        return Address(email=addr.email, name=name) if name else addr

    @property
    def now(self) -> datetime:
        assert self.run.virtual_time is not None
        # UTC whatever the DB session's TimeZone (psycopg returns timestamptz in that zone)
        return self.run.virtual_time.astimezone(UTC)

    @property
    def config(self) -> dict[str, Any]:
        return self.run.config

    def emit(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        actor: str = SIMULATOR,
        operation: str = "",
        baseline: bool = False,
    ) -> int:
        save_ids(self.run, self.gen)  # the event records the id counter it leaves behind
        return append(
            self.session,
            self.run,
            Event(type_, payload, actor=actor, operation=operation or type_, baseline=baseline),
        )


@dataclass
class DraftContent:
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    text: str
    html: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[ParsedAttachment] = field(default_factory=list)
    # what the draft answers: {"kind": "reply" | "forward", "message_id": <original>}; sending
    # it is then that operation (spec: a reply drafted then sent is a reply)
    origin: dict[str, str] | None = None


@dataclass(frozen=True)
class DraftThreading:
    """Replacement threading for update_draft: headers plus the message answered (or None)."""

    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    reply_to: str | None = None


@dataclass
class ReplayResult:
    replay_run_id: str
    matches: bool
    original_hash: str
    replay_hash: str
    event_count: int
    missing_blobs: list[str]


def _addr(email: str) -> Address:
    email = normalize_address(email)
    if "@" not in email or any(ch.isspace() or ord(ch) < 32 for ch in email):
        raise InvalidRequest(f"invalid email address {email!r}")
    return Address(email=email)


def _summary(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if isinstance(v, str | int | bool | float | list)}
    if isinstance(result, list):
        return {"count": len(result)}
    if isinstance(result, BaseModel):
        return {"id": getattr(result, "id", None)}
    return {}


class SimEngine:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        blobs: BlobStore,
        limits: MimeLimits | None = None,
    ) -> None:
        self._sf = session_factory
        self.blobs = blobs
        self.limits = limits or MimeLimits()

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._sf

    # ------------------------------------------------------------------ command wrapper

    def _command(
        self,
        scope: Scope,
        run_id: str,
        actor: str,
        operation: str,
        input_: dict[str, Any],
        fn: Callable[[Ctx], T],
        *,
        require_running: bool = True,
        mailbox_id: str | None = None,
    ) -> T:
        guarded = False
        probe = actor == AGENT and connection_test.get()
        # the interface building its response to a write this request already committed:
        # recorded, but not counted against or refused by the read/event caps (context.py)
        read_back = actor == AGENT and operation in READ_OPERATIONS and is_read_back()
        # one budgeted read per agent request (context.py, issue #24): once the request paid
        # its read, its later reads are recorded but neither counted nor refused by the read cap
        paid = (actor == AGENT and not probe and operation in READ_OPERATIONS and not read_back
                and is_read_paid())  # fmt: skip
        try:
            with self._sf.begin() as s:
                run = lock_run(s, scope, run_id)
                seq_start = run.next_seq
                ctx = Ctx(s, run, id_generator(run))
                self._guard(ctx, actor, operation, mailbox_id, require_running, uncapped=read_back,
                            read_paid=paid)  # fmt: skip
                if probe and operation not in READ_OPERATIONS:
                    raise PermissionDenied("a connection test may only read")
                guarded = True
                try:
                    result = fn(ctx)
                except (ValueError, OverflowError, RecursionError) as exc:
                    # untyped failures (e.g. header injection) become audited INVALID_REQUESTs
                    raise InvalidRequest(f"invalid input: {exc}"[:300]) from exc
                if probe and (run.next_seq != seq_start or ctx.gen.counter != run.id_counter):
                    # wrote an event (e.g. a read that marks mail seen) or drew ids (which
                    # would need an ids.reserved event): not read-only
                    raise PermissionDenied("a connection test may only read")
                if actor == AGENT and not probe:
                    if counts_against_budget(operation):
                        ctx.emit(
                            "agent.action_counted",
                            {"operation": operation, "outcome": "ok"},
                            actor=AGENT,
                            operation=operation,
                        )
                    for triggered in (operation, *ctx.also_triggers):
                        self._arm_triggers(ctx, triggered)
                    if not read_back and not paid:
                        self._count_usage(run, operation)
                if ctx.gen.counter != run.id_counter:
                    # ids were drawn after the last event: record it so replay stays exact
                    ctx.emit("ids.reserved", {"counter": ctx.gen.counter}, actor=actor,
                             operation=operation)  # fmt: skip
                s.add(
                    m.ActionRow(
                        run_id=run.id,
                        interface=current_interface.get(),
                        actor=CONNECTION_TEST if probe else actor,
                        operation=operation,
                        input=input_,
                        result=_summary(result),
                        outcome="ok",
                        virtual_time=run.virtual_time,
                        seq_start=seq_start,
                        seq_end=run.next_seq - 1,
                    )
                )
            if actor == AGENT and not probe and operation not in READ_OPERATIONS:
                note_agent_write()  # committed: this request's later reads are read-backs
            elif actor == AGENT and not probe and not read_back:
                note_agent_read()  # counted (or already paid): the request's read is paid
            return result
        except SimError as exc:
            # Budget rule: every agent command that passed the permission/mailbox/limit guard
            # consumes budget, whether it then succeeds or fails.
            usage = guarded and actor == AGENT and not probe and not read_back and not paid
            self._record_denied(
                scope,
                run_id,
                actor,
                operation,
                input_,
                exc,
                count=guarded and actor == AGENT and not probe and counts_against_budget(operation),
                usage=usage,
            )
            if usage and operation in READ_OPERATIONS:
                note_agent_read()  # a failed read still paid the request's read
            raise

    @staticmethod
    def _count_usage(run: m.Run, operation: str) -> None:
        """Resource-cap counters of a guarded agent command, kept on the run row in the same
        transaction as the audit row that records it (no event: reads and sent_copy write
        none). Successful or failed alike, like the write budget; refused by the guard: no.
        Reads: once per agent request (the caller skips the request's later reads)."""
        if operation in READ_OPERATIONS:
            run.agent_reads += 1
        elif operation == "sent_copy":
            run.agent_sent_copies += 1

    def _record_denied(
        self,
        scope: Scope,
        run_id: str,
        actor: str,
        operation: str,
        input_: dict[str, Any],
        exc: SimError,
        count: bool = False,
        usage: bool = False,
    ) -> None:
        with self._sf.begin() as s:
            try:
                run = lock_run(s, scope, run_id)  # exact placement in the log
            except NotFound:
                return
            seq_start = run.next_seq
            if isinstance(exc, ExternalDeliveryBlocked):
                # a blocked attempt is still an attempt: evidence for the delivery invariants
                # (nothing is delivered; the payload names the refused recipients only)
                append(s, run, self._blocked_event(run, actor, operation, exc))
            if count:
                append(
                    s,
                    run,
                    Event(
                        "agent.action_counted",
                        {"operation": operation, "outcome": str(exc.code)},
                        actor=AGENT,
                        operation=operation,
                        outcome=str(exc.code),
                    ),
                )
            if usage:
                self._count_usage(run, operation)
            s.add(
                m.ActionRow(
                    run_id=run.id,
                    interface=current_interface.get(),
                    actor=CONNECTION_TEST if actor == AGENT and connection_test.get() else actor,
                    operation=operation,
                    input=input_,
                    result=exc.to_dict(),
                    outcome=str(exc.code),
                    virtual_time=run.virtual_time,
                    seq_start=seq_start,
                    seq_end=run.next_seq - 1,
                )
            )

    @staticmethod
    def _blocked_event(run: m.Run, actor: str, operation: str,
                       exc: ExternalDeliveryBlocked) -> Event:  # fmt: skip
        listed = exc.details.get("recipients")
        recipients = [str(r) for r in listed] if isinstance(listed, list) else []
        return Event("delivery.external_blocked",
                     {"recipients": recipients, "mode": str(run.config.get("external_delivery")),
                      "handled": "blocked"},
                     actor=actor, operation=operation, outcome=str(exc.code))  # fmt: skip

    def record_external_blocked(self, scope: Scope, run_id: str, mailbox_id: str,
                                recipients: list[str], *, actor: str = AGENT) -> None:  # fmt: skip
        """A send attempt an interface refused at the envelope (SMTP `RCPT TO` outside the
        simulation), recorded exactly like a blocked REST send: the normal command guard
        (RUNNING run, `send` permission, own mailbox, budget), then an EXTERNAL_DELIVERY_BLOCKED
        `send` action that counts against the budget, with its `delivery.external_blocked`
        evidence event. Nothing is delivered. Always raises the refusal it recorded."""
        rcpts = [normalize_address(r) for r in recipients]

        def fn(c: Ctx) -> None:
            raise ExternalDeliveryBlocked(f"delivery to {', '.join(rcpts)} is not permitted",
                                          details={"recipients": rcpts})  # fmt: skip

        self._command(scope, run_id, actor, "send", {"to": rcpts, "stage": "envelope"}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    @staticmethod
    def _permitted(ctx: Ctx, operation: str) -> bool:
        agent = ctx.config["agent"]
        return operation not in agent["prohibited"] and (
            not agent["allowed"] or operation in agent["allowed"]
        )

    def _guard(
        self,
        ctx: Ctx,
        actor: str,
        operation: str,
        mailbox_id: str | None,
        require_running: bool,
        *,
        uncapped: bool = False,
        read_paid: bool = False,
    ) -> None:
        run = ctx.run
        if run.replay_of is not None and not (actor != AGENT and operation in READ_OPERATIONS):
            # a replay copy is never live, whatever status it was stored with (replays made
            # before REPLAYED existed stayed READY/RUNNING): only operator reads are allowed
            raise RunNotActive(f"run {run.id} is a replay of {run.replay_of}; it takes no commands")
        if require_running and run.status != RunStatus.RUNNING:
            raise RunNotActive(f"run is {run.status}, not RUNNING")
        if actor != AGENT:
            return
        agent = ctx.config["agent"]
        if operation not in UNGATED_AGENT_OPERATIONS and not self._permitted(ctx, operation):
            raise PermissionDenied(f"operation {operation!r} is not permitted for the agent")
        if mailbox_id is not None:
            mb = ctx.session.get(m.Mailbox, (mailbox_id, run.id))
            if mb is None:
                raise NotFound(f"mailbox {mailbox_id} not found")
            if mb.owner_id != agent["mailbox_owner"]:
                raise PermissionDenied("agent may only access its assigned mailbox")
        limit = ctx.config["limits"]["max_agent_actions"]
        if counts_against_budget(operation) and run.agent_actions >= limit:
            raise _limit_exceeded("agent action", "max_agent_actions", limit)
        if connection_test.get() or uncapped:
            # an operator's read-only probe, the read-back of a committed write, or a session
            # lookup at login: never counted, so never capped
            return
        # per-run resource caps (spec 10): checked before the command, so one command may
        # overshoot max_agent_events by the events it writes itself
        if operation in READ_OPERATIONS and not read_paid:  # one read unit per agent request
            limit = run_cap(ctx.config, "max_agent_reads")
            if run.agent_reads >= limit:
                raise _limit_exceeded("agent read", "max_agent_reads", limit)
        if operation == "sent_copy":
            limit = run_cap(ctx.config, "max_sent_copies")
            if run.agent_sent_copies >= limit:
                raise _limit_exceeded("sent copy", "max_sent_copies", limit)
        limit = run_cap(ctx.config, "max_agent_events")
        if run.agent_events >= limit:
            raise _limit_exceeded("agent event", "max_agent_events", limit)

    def _arm_triggers(self, ctx: Ctx, operation: str) -> None:
        rows = ctx.session.execute(
            select(m.ScheduledEvent)
            .where(m.ScheduledEvent.run_id == ctx.run.id, m.ScheduledEvent.status == "pending")
            .order_by(m.ScheduledEvent.order_key)
        ).scalars()
        for row in list(rows):
            trig = row.trigger
            if (
                trig["type"] == "after_agent_action"
                and TRIGGER_ACTIONS.get(trig["action"]) == operation
            ):
                due = ctx.now + timedelta(seconds=trig.get("delay_seconds", 0))
                ctx.emit("event.armed", {"id": row.id, "due_at": iso(due)}, operation="arm")

    # ---------------------------------------------------------------------- lookups

    def _mailbox(self, ctx: Ctx, mailbox_id: str) -> m.Mailbox:
        mb = ctx.session.get(m.Mailbox, (mailbox_id, ctx.run.id))
        if mb is None:
            raise NotFound(f"mailbox {mailbox_id} not found")
        return mb

    def _folder(self, ctx: Ctx, mailbox_id: str, system_type: str) -> m.Folder:
        row = ctx.session.execute(
            select(m.Folder).where(
                m.Folder.run_id == ctx.run.id,
                m.Folder.mailbox_id == mailbox_id,
                m.Folder.system_type == system_type,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound(f"folder {system_type} not found")
        return row

    def _message(self, ctx: Ctx, mailbox_id: str, message_id: str) -> m.Message:
        row = ctx.session.get(m.Message, (message_id, ctx.run.id))
        if row is None or row.mailbox_id != mailbox_id:
            raise NotFound(f"message {message_id} not found")
        return row

    def _emails(self, ctx: Ctx) -> dict[str, str]:
        rows = ctx.session.execute(
            select(m.Mailbox.email, m.Mailbox.id).where(m.Mailbox.run_id == ctx.run.id)
        )
        return {e: i for e, i in rows}

    def _candidates(self, ctx: Ctx, mailbox_id: str) -> list[ThreadCandidate]:
        rows = ctx.session.execute(
            select(
                m.Message.message_id_header,
                m.Message.thread_id,
                m.Message.in_reply_to,
                m.Message.references,
                m.Thread.subject_key,
            )
            .join(
                m.Thread,
                (m.Thread.run_id == m.Message.run_id) & (m.Thread.id == m.Message.thread_id),
            )
            .where(m.Message.run_id == ctx.run.id, m.Message.mailbox_id == mailbox_id)
            .order_by(m.Message.id)
        )
        return [ThreadCandidate(a, b, c, tuple(d), e) for a, b, c, d, e in rows]

    # --------------------------------------------------------------- message insertion

    def _insert_raw(
        self,
        ctx: Ctx,
        mailbox_id: str,
        raw: bytes,
        *,
        event_type: str,
        folder_type: str,
        flags: Sequence[str] = (),
        received_at: datetime | None,
        bcc: list[Address] | None = None,
        is_draft: bool = False,
        actor: str = SIMULATOR,
        operation: str = "",
        baseline: bool = False,
        provider_ids: dict[str, Any] | None = None,
    ) -> str:
        parsed = parse_mime(raw, self.limits)
        if parsed.from_ is None:
            raise InvalidRequest("message has no From header")
        raw_ref = self.blobs.put(raw)
        domain = ctx.config["domain"]
        header = parsed.message_id or ctx.gen.message_id_header(domain)
        decision = decide_thread(
            message_id_header=header,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references,
            subject=parsed.subject,
            existing=self._candidates(ctx, mailbox_id),
        )
        new_thread = None
        if decision.thread_id is None:
            thread_id = ctx.gen.next("thr")
            new_thread = {"id": thread_id, "subject_key": subject_key(parsed.subject)}
        else:
            thread_id = decision.thread_id
        message_id = ctx.gen.next("msg")
        folder = self._folder(ctx, mailbox_id, folder_type)
        attachments = []
        for att in parsed.attachments:
            attachments.append(self._attachment_payload(ctx, att))
        headers: dict[str, list[str]] = {}
        for k, v in parsed.headers:
            headers.setdefault(k, []).append(v)
        payload = {
            "message": {
                "id": message_id,
                "mailbox_id": mailbox_id,
                "message_id_header": header,
                "thread_id": thread_id,
                "folder_id": folder.id,
                "from": parsed.from_.model_dump(),
                "to": [a.model_dump() for a in parsed.to],
                "cc": [a.model_dump() for a in parsed.cc],
                "bcc": [a.model_dump() for a in (bcc if bcc is not None else parsed.bcc)],
                "subject": parsed.subject,
                "body_text": parsed.body_text,
                "body_html": parsed.body_html,
                "in_reply_to": parsed.in_reply_to,
                "references": parsed.references,
                "created_at": iso(parsed.date or ctx.now),
                "received_at": iso(received_at) if received_at else None,
                "labels": [],
                "flags": sorted(flags),
                "headers": headers,
                "is_draft": is_draft,
                "raw_blob_ref": raw_ref,
                "attachments": attachments,
                "provider_ids": provider_ids or {},
            },
            "new_thread": new_thread,
            "merged_threads": decision.merged_threads,
        }
        ctx.emit(event_type, payload, actor=actor, operation=operation, baseline=baseline)
        return message_id

    def _attachment_payload(self, ctx: Ctx, att: ParsedAttachment) -> dict[str, Any]:
        return AttachmentRef(
            id=ctx.gen.next("att"),
            filename=att.filename,
            mime_type=att.mime_type,
            size=len(att.content),
            checksum=att.checksum,
            blob_ref=self.blobs.put(att.content),
            inline=att.inline,
            content_id=att.content_id,
        ).model_dump()

    # ------------------------------------------------------------------ run lifecycle

    def create_run(
        self,
        scope: Scope,
        scenario_version_id: str,
        *,
        checks: str | None = None,
        agent_build: str | None = None,
    ) -> str:
        """Validate the stored scenario version, create an isolated run and seed it (READY)."""
        failure: InvalidRequest | None = None
        with self._sf.begin() as s:
            get_project(s, scope)
            sv = get_scenario_version(s, scope, scenario_version_id)
            body = ScenarioDoc.model_validate(sv.spec).scenario
            run = m.Run(
                id=new_control_id("run"),
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                scenario_version_id=sv.id,
                status=str(RunStatus.DRAFT),
                seed=sv.seed,
                config=self._config(body, checks=checks, agent_build=agent_build),
                next_seq=1,
            )
            s.add(run)
            s.flush()
            ctx = Ctx(s, run, id_generator(run))
            ctx.emit(
                "run.created",
                {
                    "scenario_version_id": sv.id,
                    "scenario_key": body.id,
                    "scenario_version": body.version,
                    "seed": sv.seed,
                    "config": run.config,
                },
                actor=CONTROLLER,
                operation="create_run",
            )

            def fixture_check(path: str) -> str | None:
                ref = sv.fixtures.get(path)
                if ref is None or not self.blobs.exists(ref):
                    return f"fixture {path} is not stored with this scenario version"
                return None

            report = validate_semantics(body, fixture_check)
            if report.errors:
                self._status(ctx, RunStatus.ERROR)
                failure = InvalidRequest(
                    "scenario validation failed",
                    details={"errors": [str(e) for e in report.errors]},
                )
            else:
                self._status(ctx, RunStatus.VALIDATED)
                self._seed(ctx, body, sv)
                self._status(ctx, RunStatus.READY)
            save_ids(run, ctx.gen)
            s.add(
                m.ActionRow(
                    run_id=run.id,
                    interface=current_interface.get(),
                    actor=CONTROLLER,
                    operation="create_run",
                    input={"scenario_version_id": sv.id},
                    result={"status": run.status},
                    outcome="ok" if failure is None else str(failure.code),
                    virtual_time=run.virtual_time,
                    seq_start=1,
                    seq_end=run.next_seq - 1,
                )
            )
            run_id = run.id
        if failure is not None:
            raise failure
        return run_id

    @staticmethod
    def _config(
        body: ScenarioBody, *, checks: str | None = None, agent_build: str | None = None
    ) -> dict[str, Any]:
        if checks not in (None, "on", "off"):
            raise InvalidRequest("checks must be 'on', 'off' or omitted")
        if agent_build is not None and not (0 < len(agent_build) <= 200 and agent_build.strip()):
            raise InvalidRequest("agent_build must be 1-200 characters, not only whitespace")
        if agent_build == NO_BUILD:  # the accuracy report's name for runs without a build
            raise InvalidRequest(f"agent_build {NO_BUILD!r} is reserved: omit agent_build instead")
        return {
            # post-run evaluation preferences only; never contains the answer key
            "evaluation": {"checks": checks, "agent_build": agent_build},
            "domain": body.organization.domain,
            "external_delivery": body.environment.delivery.external_delivery,
            "agent": {
                "mailbox_owner": body.agent.mailbox,
                "allowed": body.agent.allowed_operations,
                "prohibited": body.agent.prohibited_operations,
            },
            "limits": body.evaluation.limits.model_dump(),
        }

    def _status(self, ctx: Ctx, target: RunStatus) -> None:
        check_transition(RunStatus(ctx.run.status), target)
        ctx.emit(
            "run.status_changed",
            {"from": ctx.run.status, "to": str(target)},
            actor=CONTROLLER,
            operation="status",
        )

    def _seed(self, ctx: Ctx, body: ScenarioBody, sv: m.ScenarioVersion) -> None:

        def emit(t: str, p: dict[str, Any]) -> None:
            ctx.emit(t, p, operation="seed", baseline=True)

        gen = ctx.gen
        emit("clock.set", {"at": body.environment.clock.start_at, "initial": True})
        org = body.organization
        emit(
            "org.created",
            {
                "id": gen.next("org"),
                "name": org.name or org.domain,
                "domain": org.domain,
                "policies": [p.model_dump() for p in org.policies],
            },
        )
        for p in body.personas:
            emit(
                "persona.created",
                {
                    "id": p.id,
                    "email": p.email.lower(),
                    "role": p.role,
                    "knowledge_scope": p.knowledge,
                    "response_policy": p.behavior.model_dump(),
                },
            )
        for r in body.relationships:
            emit(
                "relationship.created",
                {
                    "id": gen.next("rel"),
                    "source_persona": r.source,
                    "target_persona": r.target,
                    "type": r.type,
                    "trust_level": r.trust_level,
                },
            )
        for wp in body.projects:
            emit("workproject.created", wp.model_dump())
        emails = {p.id: p.email.lower() for p in body.personas}
        mailbox_ids: dict[str, str] = {}
        for mb in body.mailboxes:
            mbid = gen.next("mbx")
            mailbox_ids[mb.owner] = mbid
            emit(
                "mailbox.created",
                {
                    "id": mbid,
                    "owner_id": mb.owner,
                    "email": emails[mb.owner],
                    "provider_profile": "neutral:1",
                    "settings": {},
                    "folders": [
                        {"id": gen.next("fld"), "name": name, "system_type": st}
                        for name, st in SYSTEM_FOLDERS
                    ],
                },
            )
        for mb in body.mailboxes:
            for im in mb.initial_messages:
                raw = self.blobs.get(sv.fixtures[im.fixture])
                self._insert_raw(
                    ctx,
                    mailbox_ids[mb.owner],
                    raw,
                    event_type="message.delivered",
                    folder_type="inbox",
                    received_at=None,
                    operation="seed",
                    baseline=True,
                )
        for idx, ev in enumerate(body.events):
            trig = ev.trigger
            due: str | None = None
            if trig.type == "at":
                assert trig.at is not None
                due = iso(parse_ts(trig.at))
            elif trig.type == "after_seconds":
                assert trig.seconds is not None
                due = iso(
                    parse_ts(body.environment.clock.start_at) + timedelta(seconds=trig.seconds)
                )
            emit(
                "event.scheduled",
                {
                    "id": ev.id,
                    "order_key": idx,
                    "due_at": due,
                    "trigger": trig.model_dump(),
                    "spec": ev.event.model_dump(by_alias=True),
                    "status": "pending",
                },
            )
        emit("run.seeded", {"scenario_key": body.id})

    def start_run(self, scope: Scope, run_id: str) -> None:
        self._command(scope, run_id, CONTROLLER, "start", {},
                      lambda c: self._status(c, RunStatus.RUNNING), require_running=False)  # fmt: skip

    def cancel_run(self, scope: Scope, run_id: str) -> None:
        self._command(scope, run_id, CONTROLLER, "cancel", {},
                      lambda c: self._status(c, RunStatus.CANCELLED), require_running=False)  # fmt: skip

    def fail_run_with_error(self, scope: Scope, run_id: str, reason: str) -> None:
        """Mark a run ERROR (infrastructure/harness failure). Not a test verdict."""

        def fn(c: Ctx) -> None:
            self._status(c, RunStatus.ERROR)

        self._command(scope, run_id, CONTROLLER, "error", {"reason": reason[:500]}, fn,
                      require_running=False)  # fmt: skip

    # ---------------------------------------------------------------- reset and replay

    def reset_run(self, scope: Scope, run_id: str) -> int:
        """Restore the scenario baseline: new epoch, same run, history kept in the log."""

        def fn(c: Ctx) -> int:
            if c.run.status not in (RunStatus.READY, RunStatus.RUNNING):
                raise Conflict(
                    f"run is {c.run.status}; only READY or RUNNING runs can be reset",
                    details={"status": c.run.status},
                )
            baseline = (
                c.session.execute(
                    select(m.EventRow)
                    .where(m.EventRow.run_id == c.run.id, m.EventRow.baseline.is_(True))
                    .order_by(m.EventRow.sequence)
                )
                .scalars()
                .all()
            )
            if not baseline:  # pragma: no cover - READY/RUNNING runs always have a baseline
                raise Conflict("run has no baseline to restore")
            epoch = c.run.epoch + 1
            c.emit("run.reset", {"epoch": epoch, "from_epoch": c.run.epoch},
                   actor=CONTROLLER, operation="reset")  # fmt: skip
            for ev in baseline:
                c.emit(ev.type, ev.payload, actor=ev.actor, operation=ev.operation)
            return epoch

        return self._command(scope, run_id, CONTROLLER, "reset", {}, fn, require_running=False)

    def replay_run(self, scope: Scope, run_id: str) -> ReplayResult:
        """Rebuild state in a fresh run from the recorded events/artifacts only.

        Nothing is regenerated: payloads carry ids, timestamps and blob references. The
        result states whether the replayed state hash equals the original's (compared before
        the replay is closed). The replay run (`replay_of` set) is then closed with a final
        `run.status_changed` to REPLAYED unless the copied log already ended the run.
        """
        with self._sf.begin() as s:
            orig = lock_run(s, scope, run_id)
            events = (
                s.execute(
                    select(m.EventRow)
                    .where(m.EventRow.run_id == orig.id)
                    .order_by(m.EventRow.sequence)
                )
                .scalars()
                .all()
            )
            original_hash = state_hash(snapshot(s, orig.id))
            replay = m.Run(
                id=new_control_id("run"),
                tenant_id=orig.tenant_id,
                project_id=orig.project_id,
                scenario_version_id=orig.scenario_version_id,
                status=str(RunStatus.DRAFT),
                seed=orig.seed,
                config=orig.config,
                next_seq=1,
                replay_of=orig.id,
            )
            s.add(replay)
            s.flush()
            missing: list[str] = []
            for ev in events:
                append(
                    s,
                    replay,
                    Event(ev.type, ev.payload, actor=ev.actor, operation=ev.operation,
                          outcome=ev.outcome, baseline=ev.baseline, virtual_time=ev.virtual_time,
                          id_counter_after=ev.id_counter_after),
                )  # fmt: skip
                missing.extend(self._missing_blobs(ev.payload))
            replay_hash = state_hash(snapshot(s, replay.id))
            if RunStatus(replay.status) not in TERMINAL:
                # a replay is a frozen copy of a recording, never a live agent run: it ends in
                # REPLAYED (an original that had already ended keeps its final status)
                append(s, replay, Event("run.status_changed",
                                        {"from": replay.status, "to": str(RunStatus.REPLAYED)},
                                        actor=CONTROLLER, operation="replay"))  # fmt: skip
            s.add(
                m.ActionRow(
                    run_id=orig.id,
                    interface=current_interface.get(),
                    actor=CONTROLLER,
                    operation="replay",
                    input={},
                    result={"replay_run_id": replay.id, "matches": original_hash == replay_hash},
                    outcome="ok",
                    virtual_time=orig.virtual_time,
                    seq_start=orig.next_seq,
                    seq_end=orig.next_seq - 1,
                )
            )
            return ReplayResult(
                replay_run_id=replay.id,
                matches=original_hash == replay_hash and not missing,
                original_hash=original_hash,
                replay_hash=replay_hash,
                event_count=len(events),
                missing_blobs=sorted(set(missing)),
            )

    def _missing_blobs(self, payload: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        msg = payload.get("message")
        if isinstance(msg, dict):
            if msg.get("raw_blob_ref"):
                refs.append(msg["raw_blob_ref"])
            refs.extend(a["blob_ref"] for a in msg.get("attachments", []))
        if payload.get("raw_blob_ref"):
            refs.append(payload["raw_blob_ref"])
        return [r for r in refs if not self.blobs.exists(r)]

    # ------------------------------------------------------------------ inspection

    def state(self, scope: Scope, run_id: str) -> dict[str, Any]:
        with self._sf() as s:
            get_run(s, scope, run_id)
            return snapshot(s, run_id)

    def state_hash(self, scope: Scope, run_id: str) -> str:
        return state_hash(self.state(scope, run_id))

    def events(self, scope: Scope, run_id: str) -> list[dict[str, Any]]:
        with self._sf() as s:
            get_run(s, scope, run_id)
            rows = s.execute(
                select(m.EventRow).where(m.EventRow.run_id == run_id).order_by(m.EventRow.sequence)
            ).scalars()
            return [
                {
                    "sequence": r.sequence, "epoch": r.epoch, "type": r.type, "actor": r.actor,
                    "operation": r.operation, "outcome": r.outcome, "payload": r.payload,
                    "virtual_time": iso(r.virtual_time) if r.virtual_time else None,
                }
                for r in rows
            ]  # fmt: skip

    @staticmethod
    def _action_dict(r: m.ActionRow) -> dict[str, Any]:
        return {"id": r.id, "actor": r.actor, "operation": r.operation, "input": r.input,
                "result": r.result, "outcome": r.outcome,
                "virtual_time": iso(r.virtual_time) if r.virtual_time else None,
                "seq_start": r.seq_start, "seq_end": r.seq_end,
                "interface": r.interface}  # fmt: skip

    def actions(self, scope: Scope, run_id: str) -> list[dict[str, Any]]:
        with self._sf() as s:
            get_run(s, scope, run_id)
            rows = s.execute(
                select(m.ActionRow).where(m.ActionRow.run_id == run_id).order_by(m.ActionRow.id)
            ).scalars()
            return [self._action_dict(r) for r in rows]

    def actions_page(
        self, scope: Scope, run_id: str, *, after_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Recorded actions with id > `after_id`, oldest first, at most `limit` (polling)."""
        check_page_size(limit)
        with self._sf() as s:
            get_run(s, scope, run_id)
            rows = s.execute(
                select(m.ActionRow)
                .where(m.ActionRow.run_id == run_id, m.ActionRow.id > after_id)
                .order_by(m.ActionRow.id)
                .limit(limit)
            ).scalars()
            return [self._action_dict(r) for r in rows]

    def agent_mailbox_owner(self, scope: Scope, run_id: str) -> str:
        """Persona id whose mailbox the agent operates (never impersonated by personas)."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            return str(run.config["agent"]["mailbox_owner"])

    def persona_generations(self, scope: Scope, run_id: str) -> list[dict[str, Any]]:
        """Recorded persona outputs of the current epoch (persona.generated payloads)."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            rows = s.execute(
                select(m.EventRow).where(m.EventRow.run_id == run.id, m.EventRow.epoch == run.epoch,
                                         m.EventRow.type.in_(("persona.generated",
                                                              "persona.generation_failed")))
                .order_by(m.EventRow.sequence)
            ).scalars()  # fmt: skip
            return [{"sequence": r.sequence, "type": r.type, **r.payload} for r in rows]

    def run_info(self, scope: Scope, run_id: str) -> dict[str, Any]:
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            return {
                "id": run.id, "status": run.status, "epoch": run.epoch,
                "virtual_time": iso(run.virtual_time) if run.virtual_time else None,
                "agent_actions": run.agent_actions, "replay_of": run.replay_of,
            }  # fmt: skip

    def mailbox_id_for(self, scope: Scope, run_id: str, persona_id: str) -> str:
        with self._sf() as s:
            get_run(s, scope, run_id)
            row = s.execute(
                select(m.Mailbox.id).where(
                    m.Mailbox.run_id == run_id, m.Mailbox.owner_id == persona_id
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFound(f"no mailbox for persona {persona_id}")
            return row

    # ---------------------------------------------------------------- simulator control

    def inject(
        self,
        scope: Scope,
        run_id: str,
        mailbox_id: str,
        message: dict[str, Any] | bytes,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Inject an inbound message into a simulated mailbox. Never contacts anything real."""

        def fn(c: Ctx) -> dict[str, Any]:
            self._mailbox(c, mailbox_id)
            if isinstance(message, bytes):
                raw = message
            else:
                try:
                    raw = build_mime(
                        message_id=c.gen.message_id_header(c.config["domain"]),
                        from_=c.named(_addr(message["from"])),
                        to=[c.named(_addr(a)) for a in message["to"]],
                        cc=[c.named(_addr(a)) for a in message.get("cc", [])],
                        subject=message.get("subject", ""),
                        date=c.now,
                        text=message.get("text"),
                        html=message.get("html"),
                        in_reply_to=message.get("in_reply_to"),
                        references=message.get("references"),
                    )
                except KeyError as exc:
                    raise InvalidRequest(f"missing field {exc}") from None
            mid = self._insert_raw(
                c, mailbox_id, raw, event_type="message.delivered", folder_type="inbox",
                received_at=c.now, actor=CONTROLLER, operation="inject",
            )  # fmt: skip
            return {
                "event_id": event_id or c.gen.next("evt"),
                "message_id": mid,
                "delivery_status": "delivered_to_simulated_mailbox",
            }

        return self._command(scope, run_id, CONTROLLER, "inject",
                             {"mailbox_id": mailbox_id, "bytes": isinstance(message, bytes)}, fn)  # fmt: skip

    def tick(self, scope: Scope, run_id: str) -> dict[str, Any]:
        return self._command(scope, run_id, CONTROLLER, "tick", {}, lambda c: self._advance(c, 0))

    def advance(self, scope: Scope, run_id: str, seconds: float, *,
                stop_at: datetime | None = None) -> dict[str, Any]:  # fmt: skip
        """Move the clock by `seconds`, firing due scheduled events at their due times.

        `stop_at` pauses the move early (the whole `seconds` is still validated, so an
        over-limit advance fails before anything moves): the persona runtime uses it to stop
        at each persona reply's due time and stamp the reply then (`virtual_time` says where
        the clock stopped)."""
        input_: dict[str, Any] = {"seconds": seconds if math.isfinite(seconds) else str(seconds)}
        if stop_at is not None:
            input_["stop_at"] = iso(stop_at)
        return self._command(scope, run_id, CONTROLLER, "advance", input_,
                             lambda c: self._advance(c, seconds, stop_at))  # fmt: skip

    def _advance(self, c: Ctx, seconds: float, stop_at: datetime | None = None) -> dict[str, Any]:
        if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_ADVANCE_SECONDS:
            raise InvalidRequest("seconds must be a finite, non-negative duration")
        start = c.run.start_time
        assert start is not None
        target = c.now + timedelta(seconds=seconds)
        limit = c.config["limits"]["max_virtual_duration_seconds"]
        if (target - start).total_seconds() > limit:
            raise SimulationLimitExceeded(
                f"virtual duration limit of {limit}s exceeded",
                details={"limit": limit, "cap": "max_virtual_duration_seconds"},
            )
        if stop_at is not None:
            target = max(c.now, min(target, parse_ts(stop_at)))
        items = c.session.execute(
            select(m.ScheduledEvent).where(m.ScheduledEvent.run_id == c.run.id)
        ).scalars()
        rows = {r.id: r for r in items}
        due = due_in_order(
            [Scheduled(r.id, r.due_at, r.order_key, r.status) for r in rows.values()], target
        )
        fired: list[str] = []
        for item in due:
            assert item.due_at is not None
            if item.due_at > c.now:
                c.emit("clock.advanced", {"from": iso(c.now), "to": iso(item.due_at)},
                       actor=CONTROLLER, operation="advance")  # fmt: skip
            self._fire(c, rows[item.id])
            fired.append(item.id)
        if target > c.now:
            c.emit("clock.advanced", {"from": iso(c.now), "to": iso(target)},
                   actor=CONTROLLER, operation="advance")  # fmt: skip
        return {"fired": fired, "virtual_time": iso(c.now)}

    def _fire(self, c: Ctx, row: m.ScheduledEvent) -> None:
        spec = row.spec
        personas = {
            p.id: p.email
            for p in c.session.execute(
                select(m.Persona).where(m.Persona.run_id == c.run.id)
            ).scalars()
        }
        mailboxes = {
            mb.owner_id: mb.id
            for mb in c.session.execute(
                select(m.Mailbox).where(m.Mailbox.run_id == c.run.id)
            ).scalars()
        }
        recipients = (
            [spec["to"]] if isinstance(spec["to"], str) else list(dict.fromkeys(spec["to"]))
        )
        raw = build_mime(
            message_id=c.gen.message_id_header(c.config["domain"]),
            from_=c.named(Address(email=personas[spec["from"]])),
            to=[c.named(Address(email=personas[r])) for r in recipients],
            subject=spec["subject"],
            date=c.now,
            text=spec["body"],
        )
        for r in recipients:
            self._insert_raw(
                c, mailboxes[r], raw, event_type="message.delivered", folder_type="inbox",
                received_at=c.now, operation="email.receive",
            )  # fmt: skip
        c.emit("event.fired", {"id": row.id}, operation="fire")

    def persona_reply(
        self,
        scope: Scope,
        run_id: str,
        persona_id: str,
        *,
        to: list[str],
        subject: str,
        text: str,
        generation: dict[str, Any],
        in_reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Deliver a persona-authored message (scripted or LLM) and RECORD its generation.

        `generation` is the provenance (mode scripted|llm, provider, model, prompt_hash, token
        counts). The full output is stored in a `persona.generated` event, so replay consumes
        the recording and never calls a model (spec 6.3). Delivery follows the same isolation
        rules as any send: simulated mailboxes only; outsiders per the external mode.
        """
        allowed_meta = {"mode", "provider", "model", "prompt_hash", "input_tokens",
                        "output_tokens", "rule_index", "llm_call_id"}  # fmt: skip
        if generation.get("mode") not in ("scripted", "llm"):
            raise InvalidRequest("generation.mode must be 'scripted' or 'llm'")
        unknown = set(generation) - allowed_meta
        if unknown:
            raise InvalidRequest(f"unknown generation fields: {sorted(unknown)}")

        def fn(c: Ctx) -> dict[str, Any]:
            persona = c.session.get(m.Persona, (persona_id, c.run.id))
            if persona is None:
                raise NotFound(f"persona {persona_id} not found")
            irt: str | None = None
            refs: list[str] = []
            if in_reply_to_message_id is not None:
                orig = c.session.get(m.Message, (in_reply_to_message_id, c.run.id))
                if orig is None:
                    raise NotFound(f"message {in_reply_to_message_id} not found")
                irt, refs = orig.message_id_header, [*orig.references, orig.message_id_header]
                # at most one recorded outcome per (epoch, persona, message): concurrent
                # runtimes cannot double-reply (checked under the run lock)
                ev = m.EventRow
                already = c.session.scalar(
                    select(func.count()).select_from(ev).where(
                        ev.run_id == c.run.id, ev.epoch == c.run.epoch,
                        ev.type.in_(("persona.generated", "persona.generation_failed")),
                        ev.payload["persona_id"].astext == persona_id,
                        (ev.payload["in_reply_to"].astext == irt)
                        | (ev.payload["message_id"].astext == in_reply_to_message_id),
                    )
                )  # fmt: skip
                if already:
                    raise Conflict(
                        f"persona {persona_id} already answered {in_reply_to_message_id}"
                    )
            c.emit("persona.generated",
                   {"persona_id": persona_id, "generation": generation, "subject": subject,
                    "text": text, "to": to, "in_reply_to": irt},
                   operation="persona.reply")  # fmt: skip
            own = c.session.execute(
                select(m.Mailbox).where(m.Mailbox.run_id == c.run.id, m.Mailbox.owner_id == persona_id)
            ).scalar_one_or_none()  # fmt: skip
            if own is not None:  # persona has a mailbox: a normal send (Sent copy + deliveries)
                return self._send(c, own.id, to, [], [], subject, text, None, irt, refs or None,
                                  SIMULATOR, "persona.reply")  # fmt: skip
            rcpts = [_addr(a) for a in to]
            if not rcpts:
                raise InvalidRequest("at least one recipient is required")
            policy = DeliveryPolicy(c.config["external_delivery"])
            plan = policy.plan([a.email for a in rcpts], self._emails(c))
            raw = build_mime(message_id=c.gen.message_id_header(c.config["domain"]),
                             from_=c.named(Address(email=persona.email)),
                             to=[c.named(a) for a in rcpts], subject=subject,
                             date=c.now, text=text, in_reply_to=irt, references=refs or None)  # fmt: skip
            delivered = [
                self._insert_raw(c, mbid, raw, event_type="message.delivered", folder_type="inbox",
                                 received_at=c.now, operation="persona.reply")
                for _email, mbid in plan.internal
            ]  # fmt: skip
            for ext in plan.external:  # sink/event modes only (deny raised in plan())
                c.emit("delivery.external_attempt",
                       {"recipient": ext, "handled": "event_only"}, operation="persona.reply")  # fmt: skip
            return {"delivered_message_ids": delivered, "external_recipients": plan.external}

        return self._command(scope, run_id, SIMULATOR, "persona.reply",
                             {"persona_id": persona_id, "to": to, "mode": generation.get("mode"),
                              "subject_chars": len(subject), "body_chars": len(text)}, fn)  # fmt: skip

    def persona_failed(
        self,
        scope: Scope,
        run_id: str,
        persona_id: str,
        *,
        in_reply_to_message_id: str,
        reason: str,
        generation: dict[str, Any] | None = None,
    ) -> None:
        """Record that a persona could not answer a message (e.g. model refusal), so the
        runtime never retries it. Delivers nothing; the record is replayed like any event."""

        def fn(c: Ctx) -> None:
            if c.session.get(m.Persona, (persona_id, c.run.id)) is None:
                raise NotFound(f"persona {persona_id} not found")
            if c.session.get(m.Message, (in_reply_to_message_id, c.run.id)) is None:
                raise NotFound(f"message {in_reply_to_message_id} not found")
            c.emit("persona.generation_failed",
                   {"persona_id": persona_id, "message_id": in_reply_to_message_id,
                    "reason": reason[:200], "generation": generation or {}},
                   operation="persona.failed")  # fmt: skip

        self._command(scope, run_id, SIMULATOR, "persona.failed",
                      {"persona_id": persona_id, "message_id": in_reply_to_message_id}, fn)  # fmt: skip

    # ------------------------------------------------------------- mailbox: mutations

    def send(
        self,
        scope: Scope,
        run_id: str,
        mailbox_id: str,
        *,
        to: list[str],
        subject: str,
        text: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: str | None = None,
        actor: str = AGENT,
    ) -> dict[str, Any]:
        def fn(c: Ctx) -> dict[str, Any]:
            return self._send(c, mailbox_id, to, cc or [], bcc or [], subject, text, html,
                              None, None, actor, "send")  # fmt: skip

        return self._command(
            scope, run_id, actor, "send",
            {"to": to, "cc": cc or [], "bcc_count": len(bcc or []), "subject_chars": len(subject), "body_chars": len(text)},
            fn, mailbox_id=mailbox_id,
        )  # fmt: skip

    def reply(
        self,
        scope: Scope,
        run_id: str,
        mailbox_id: str,
        message_id: str,
        *,
        text: str,
        reply_all: bool = False,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        subject: str | None = None,
        html: str | None = None,
        actor: str = AGENT,
    ) -> dict[str, Any]:
        """Reply to a message: ONE `reply` operation (policy, budget, triggers, ANSWERED flag).

        Recipients and subject default to the usual reply / reply-all derivation; `to`, `cc`,
        `bcc` and `subject` override them (adapters whose wire protocol lets a client compose
        the reply, e.g. Gmail `messages.send` with In-Reply-To, or Graph reply with a message).
        Threading headers always come from the original message.
        """
        overrides = sorted(k for k, v in (("to", to), ("cc", cc), ("bcc", bcc),
                                          ("subject", subject), ("html", html)) if v is not None)  # fmt: skip

        def fn(c: Ctx) -> dict[str, Any]:
            mb = self._mailbox(c, mailbox_id)
            orig = self._message(c, mailbox_id, message_id)
            me = mb.email
            if to is not None:
                rcpt_to = list(to)
            else:
                rcpt_to = (
                    [orig.from_["email"]]
                    if orig.from_["email"] != me
                    else [a["email"] for a in orig.to]
                )
                if reply_all:
                    rcpt_to += [a["email"] for a in orig.to if a["email"] != me]
                rcpt_to = [a for a in dict.fromkeys(rcpt_to) if a != me] or [orig.from_["email"]]
            rcpt_cc: list[str] = list(cc) if cc is not None else []
            if cc is None and reply_all:
                rcpt_cc = [a["email"] for a in orig.cc if a["email"] != me]
            if not (rcpt_to or rcpt_cc or bcc):
                raise InvalidRequest("no recipients to reply to")
            subj = subject if subject is not None else (
                orig.subject if orig.subject.lower().startswith("re:") else f"Re: {orig.subject}"
            )  # fmt: skip
            refs = [*orig.references, orig.message_id_header]
            result = self._send(c, mailbox_id, rcpt_to, rcpt_cc, list(bcc or []), subj, text, html,
                                orig.message_id_header, refs, actor, "reply")  # fmt: skip
            if not is_self_addressed([*rcpt_to, *rcpt_cc, *(bcc or [])], [me]):
                # a reply only to oneself answers nothing: the original stays un-ANSWERED
                c.emit("message.flags_changed", {"mailbox_id": mailbox_id, "message_id": orig.id, "add": ["ANSWERED"], "remove": []},
                       actor=actor, operation="reply")  # fmt: skip
            return result

        return self._command(
            scope, run_id, actor, "reply",
            {"message_id": message_id, "reply_all": reply_all, "body_chars": len(text),
             "overrides": overrides},
            fn, mailbox_id=mailbox_id,
        )  # fmt: skip

    def _send(
        self,
        c: Ctx,
        mailbox_id: str,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        text: str,
        html: str | None,
        in_reply_to: str | None,
        references: list[str] | None,
        actor: str,
        operation: str,
        attachments: list[ParsedAttachment] | None = None,
    ) -> dict[str, Any]:
        mb = self._mailbox(c, mailbox_id)
        to_a, cc_a, bcc_a = [_addr(a) for a in to], [_addr(a) for a in cc], [_addr(a) for a in bcc]
        if not (to_a or cc_a or bcc_a):
            raise InvalidRequest("at least one recipient is required")
        distinct = len({a.email for a in (*to_a, *cc_a, *bcc_a)})
        if distinct > MAX_RECIPIENTS:
            raise InvalidRequest(f"too many recipients ({distinct}; at most {MAX_RECIPIENTS})",
                                 details={"recipients": distinct, "max": MAX_RECIPIENTS})  # fmt: skip
        policy = DeliveryPolicy(c.config["external_delivery"])
        plan = policy.plan([a.email for a in (*to_a, *cc_a, *bcc_a)], self._emails(c))
        if (
            actor == AGENT
            and plan.external
            and "external_delivery" in c.config["agent"]["prohibited"]
        ):
            raise ExternalDeliveryBlocked(
                f"delivery to {', '.join(plan.external)} is not permitted",
                details={"recipients": plan.external},
            )
        header = c.gen.message_id_header(c.config["domain"])
        raw = build_mime(
            message_id=header, from_=c.named(Address(email=mb.email)),
            to=[c.named(a) for a in to_a], cc=[c.named(a) for a in cc_a], subject=subject,
            date=c.now, text=text, html=html, in_reply_to=in_reply_to, references=references,
            attachments=attachments,
        )  # fmt: skip
        sent_id = self._insert_raw(
            c, mailbox_id, raw, event_type="message.sent", folder_type="sent", flags=["SEEN"],
            received_at=None, bcc=bcc_a, actor=actor, operation=operation,
        )  # fmt: skip
        delivered: list[str] = []
        for _email, mbid in plan.internal:
            delivered.append(
                self._insert_raw(
                    c, mbid, raw, event_type="message.delivered", folder_type="inbox",
                    received_at=c.now, bcc=[], actor=actor, operation=operation,
                )
            )  # fmt: skip
        sunk_ref = (
            self.blobs.put(raw) if plan.external and policy.mode is ExternalMode.SINK else None
        )  # one stored copy per send, referenced by every delivery.sunk event
        for ext in plan.external:
            if sunk_ref is not None:
                c.emit("delivery.sunk",
                       {"id": c.gen.next("snk"), "recipient": ext, "raw_blob_ref": sunk_ref,
                        "sent_at": iso(c.now)}, actor=actor, operation=operation)  # fmt: skip
            else:
                c.emit("delivery.external_attempt",
                       {"recipient": ext, "message_id_header": header, "handled": "event_only"},
                       actor=actor, operation=operation)  # fmt: skip
        return {
            "message_id": sent_id,
            "message_id_header": header,
            "delivered_message_ids": delivered,
            "external_recipients": plan.external,
            "external_mode": str(policy.mode),
        }

    def _insert_draft(self, c: Ctx, mailbox_id: str, d: DraftContent, draft_id: str,
                      actor: str) -> str:  # fmt: skip
        mb = self._mailbox(c, mailbox_id)
        raw = build_mime(
            message_id=c.gen.message_id_header(c.config["domain"]),
            from_=c.named(Address(email=mb.email)), to=[c.named(_addr(a)) for a in d.to],
            cc=[c.named(_addr(a)) for a in d.cc],
            subject=d.subject, date=c.now, text=d.text, html=d.html, in_reply_to=d.in_reply_to,
            references=d.references or None, attachments=d.attachments or None,
        )  # fmt: skip
        ids: dict[str, Any] = {"draft_id": draft_id}
        if d.origin:
            ids["origin"] = dict(d.origin)
        return self._insert_raw(
            c, mailbox_id, raw, event_type="draft.created", folder_type="drafts",
            flags=["DRAFT", "SEEN"], received_at=None, bcc=[_addr(a) for a in d.bcc], is_draft=True,
            actor=actor, operation="draft", provider_ids=ids,
        )  # fmt: skip

    def create_draft(
        self, scope: Scope, run_id: str, mailbox_id: str, *, to: list[str], subject: str,
        text: str = "", cc: list[str] | None = None, bcc: list[str] | None = None,
        html: str | None = None, in_reply_to: str | None = None,
        references: list[str] | None = None, reply_to: str | None = None,
        forward_of: str | None = None, actor: str = AGENT,
    ) -> dict[str, Any]:  # fmt: skip
        """A new draft. `reply_to` / `forward_of` name the message (internal id, this mailbox,
        not a draft) the draft answers: sending it is then ONE `reply` / `forward` with the same
        semantics as the direct operation. A reply draft gets the original's threading headers
        unless given; a forward draft carries the original's attachments."""
        if reply_to is not None and forward_of is not None:
            raise InvalidRequest("a draft answers at most one message (reply_to or forward_of)")
        content = DraftContent(
            to, cc or [], bcc or [], subject, text, html, in_reply_to, references or []
        )

        def fn(c: Ctx) -> dict[str, Any]:
            if reply_to is not None:
                self._answer(c, mailbox_id, content, reply_to, "reply")
            elif forward_of is not None:
                self._answer(c, mailbox_id, content, forward_of, "forward")
            draft_id = c.gen.next("drf")
            return {"message_id": self._insert_draft(c, mailbox_id, content, draft_id, actor),
                    "draft_id": draft_id}  # fmt: skip

        input_: dict[str, Any] = {"to": to, "subject_chars": len(subject)}
        if reply_to is not None or forward_of is not None:
            input_["reply_to" if reply_to is not None else "forward_of"] = reply_to or forward_of
        return self._command(scope, run_id, actor, "draft", input_, fn, mailbox_id=mailbox_id)

    def _answer(self, c: Ctx, mailbox_id: str, content: DraftContent, origin_id: str,
                kind: str) -> None:  # fmt: skip
        """Make `content` a reply / forward draft of `origin_id` (a non-draft of the mailbox)."""
        orig = self._message(c, mailbox_id, origin_id)
        if orig.is_draft:
            raise InvalidRequest(f"message {origin_id} is a draft; it cannot be answered")
        content.origin = {"kind": kind, "message_id": orig.id}
        if kind == "reply" and content.in_reply_to is None:
            content.in_reply_to = orig.message_id_header
            content.references = content.references or [*orig.references, orig.message_id_header]
        if kind == "forward":
            content.attachments = self._blob_attachments(c, orig.id)

    def move(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, folder: str,
             *, actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            target = self._resolve_folder(c, mailbox_id, folder)
            self._check_placement(c, row, target, actor)
            c.emit("message.moved", {"mailbox_id": mailbox_id, "message_id": message_id, "folder_id": target.id},
                   actor=actor, operation="move")  # fmt: skip

        self._command(scope, run_id, actor, "move", {"message_id": message_id, "folder": folder},
                      fn, mailbox_id=mailbox_id)  # fmt: skip

    def _check_placement(self, c: Ctx, row: m.Message, target: m.Folder, actor: str) -> None:
        """Sent and Drafts hold only what the account itself wrote: an agent may not move or
        copy received mail there (that would forge "sent"/"drafted" evidence). Mail it really
        sent may go back to Sent, a draft may go back to Drafts; a message merely claiming
        this account as its sender (e.g. appended) is not sent mail."""
        if (
            actor != AGENT
            or target.system_type not in ("sent", "drafts")
            or row.folder_id == target.id
        ):
            return
        if target.system_type == "drafts" and row.is_draft:
            return
        if target.system_type == "sent" and not row.is_draft and self._was_sent(c, row.id):
            return
        if row.is_draft:
            what = "a draft"
        elif target.system_type == "sent":  # received, appended or seeded: never sent from here
            what = "a message this account did not send"
        else:
            what = "a message that is not a draft"
        raise PermissionDenied(
            f"{what} cannot be placed in {target.name}: it holds only messages this account "
            f"{'sent' if target.system_type == 'sent' else 'drafted'}",
            details={"folder": target.system_type},
        )

    @staticmethod
    def _was_sent(c: Ctx, message_id: str) -> bool:
        """Provenance: the message was created by a real send (a `message.sent` event)."""
        hit = c.session.scalar(
            select(m.EventRow.sequence).where(
                m.EventRow.run_id == c.run.id, m.EventRow.type == "message.sent",
                m.EventRow.payload["message"]["id"].astext == message_id,
            ).limit(1)
        )  # fmt: skip
        return hit is not None

    def _resolve_folder(self, c: Ctx, mailbox_id: str, folder: str) -> m.Folder:
        rows = c.session.execute(
            select(m.Folder).where(m.Folder.run_id == c.run.id, m.Folder.mailbox_id == mailbox_id)
        ).scalars()
        for f in rows:
            if folder in (f.id, f.system_type) or folder.lower() == f.name.lower():
                return f
        raise NotFound(f"folder {folder!r} not found")

    def modify_labels(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                      add: Sequence[str] = (), remove: Sequence[str] = (), actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            if any(not x.strip() for x in (*add, *remove)):
                raise InvalidRequest("label names must not be empty")
            if actor == AGENT:  # SENT, DRAFT, INBOX, ...: derived, never set by hand
                refuse_system_labels((*add, *remove))
                self._check_label_cap(c, row, add, remove)
            c.emit("message.labels_changed",
                   {"mailbox_id": mailbox_id, "message_id": message_id, "add": list(add), "remove": list(remove)},
                   actor=actor, operation="label")  # fmt: skip

        self._command(scope, run_id, actor, "label",
                      {"message_id": message_id, "add": list(add), "remove": list(remove)}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def set_flags(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                  add: Sequence[str] = (), remove: Sequence[str] = (), actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            bad = [f for f in (*add, *remove) if f not in VALID_FLAGS]
            if bad:
                raise InvalidRequest(f"unknown flag(s): {bad}")
            if actor == AGENT:
                self._check_draft_flag(row, add, remove)
            c.emit("message.flags_changed",
                   {"mailbox_id": mailbox_id, "message_id": message_id, "add": list(add), "remove": list(remove)},
                   actor=actor, operation="flag")  # fmt: skip

        self._command(scope, run_id, actor, "flag",
                      {"message_id": message_id, "add": list(add), "remove": list(remove)}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    @staticmethod
    def _check_label_cap(c: Ctx, row: m.Message, add: Sequence[str],
                         remove: Sequence[str]) -> None:  # fmt: skip
        """max_labels_per_message: refuse an agent label change that leaves the message with
        more labels (keywords, categories) than the cap AND more than it had. Removing, and
        swapping names without adding to the count (a rename, a category replacement), are
        always allowed, also on a message already above the cap. Same set arithmetic as the
        projector."""
        current = set(row.labels)
        after = (current | set(add)) - set(remove)
        limit = run_cap(c.config, "max_labels_per_message")
        if len(after) > limit and len(after) > len(current):
            raise labels_per_message_exceeded(limit)

    def session_mailbox(self, scope: Scope, run_id: str, mailbox_id: str) -> dict[str, Any]:
        """Lookup for an IMAP/SMTP login (not an agent read): the agent's mailbox address and
        the run's latest event sequence, after the same run / permission / mailbox guard an
        agent read passes, but never the read or event caps. Those bound what an agent reads,
        not whether it may connect, so an agent at max_agent_reads can still log in and send
        over SMTP. A successful lookup is not recorded (a login is no agent action); a refused
        one is, like any denied agent attempt (e.g. another persona's mailbox). An operator's
        connection test records its probe as before."""
        if connection_test.get():
            self.mailbox_info(scope, run_id, mailbox_id)  # the probe's activity row
        try:
            with self._sf() as s:
                run = get_run(s, scope, run_id)
                ctx = Ctx(s, run, id_generator(run))
                self._guard(ctx, AGENT, "read", mailbox_id, True, uncapped=True)
                mb = self._mailbox(ctx, mailbox_id)
                return {"mailbox_id": mb.id, "email": mb.email,
                        "latest_sequence": run.next_seq - 1}  # fmt: skip
        except SimError as exc:
            self._record_denied(scope, run_id, AGENT, "read", {}, exc)
            raise

    def label_cap(self, scope: Scope, run_id: str) -> int:
        """Internal, unaudited lookup for an interface's pre-check before storing a message with
        labels (not an agent read): the run's max_labels_per_message."""
        with self._sf() as s:
            return run_cap(get_run(s, scope, run_id).config, "max_labels_per_message")

    @staticmethod
    def _check_draft_flag(row: m.Message, add: Sequence[str], remove: Sequence[str]) -> None:
        error = draft_flag_error(row.is_draft, add, remove)
        if error is not None:
            raise error

    def is_draft(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str) -> bool:
        """Internal, unaudited lookup for an interface's pre-check before a multi-write change
        (not an agent read): whether `message_id` of `mailbox_id` is a draft. NOT_FOUND like
        any command on a message outside the mailbox."""
        with self._sf() as s:
            get_run(s, scope, run_id)
            row = s.get(m.Message, (message_id, run_id))
            if row is None or row.mailbox_id != mailbox_id:
                raise NotFound(f"message {message_id} not found")
            return bool(row.is_draft)

    def sent_copy(self, scope: Scope, run_id: str, mailbox_id: str, folder: str, size: int,
                  *, actor: str = AGENT) -> None:  # fmt: skip
        """An IMAP client filing its copy of an SMTP send into Sent (Thunderbird's default):
        stores nothing, the send already filed the real copy. Recorded as `sent_copy` with
        only the RUNNING-run and own-mailbox guard: no permission gate (a scenario that
        prohibits `insert` must not penalise it), no budget, no event, no evidence."""

        def fn(c: Ctx) -> dict[str, Any]:
            return {"stored": False}

        self._command(scope, run_id, actor, "sent_copy", {"bytes": size, "folder": folder}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def record_refused(self, scope: Scope, run_id: str, mailbox_id: str, operation: str,
                       error: SimError, *, message_id: str | None = None,
                       add: Sequence[str] = (), remove: Sequence[str] = (),
                       folder: str | None = None, size: int | None = None,
                       actor: str = AGENT) -> None:  # fmt: skip
        """An agent `label` / `flag` / `insert` write an interface refused BEFORE its first
        write (system labels, the DRAFT flag, Sent/Drafts placement; see domain.models),
        recorded exactly like the engine's own refusal of that write: the command guard
        (RUNNING run, permission, own mailbox, budget), then the operation failing with
        `error`'s code, budgeted like the write. Nothing is written. Always raises (the
        guard's error, else `error`)."""
        if operation not in ("label", "flag", "insert"):
            raise ValueError(f"not an auditable refusal: {operation!r}")
        if operation == "insert":
            input_: dict[str, Any] = {"bytes": size or 0, "folder": folder or ""}
        else:
            input_ = {"message_id": message_id, "add": list(add), "remove": list(remove)}

        def fn(c: Ctx) -> None:
            raise error

        self._command(scope, run_id, actor, operation, input_, fn, mailbox_id=mailbox_id)

    def delete(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
               *, actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            self._emit_delete(c, self._message(c, mailbox_id, message_id), actor, "delete")

        self._command(scope, run_id, actor, "delete", {"message_id": message_id}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def _emit_delete(self, c: Ctx, row: m.Message, actor: str, operation: str) -> None:
        remaining = c.session.scalar(
            select(func.count()).select_from(m.Message).where(
                m.Message.run_id == c.run.id, m.Message.thread_id == row.thread_id,
                m.Message.id != row.id,
            )
        )  # fmt: skip
        c.emit("message.deleted",
               {"mailbox_id": row.mailbox_id, "message_id": row.id,
                "empty_thread": None if remaining else row.thread_id},
               actor=actor, operation=operation)  # fmt: skip

    def _blob_attachments(self, c: Ctx, message_id: str) -> list[ParsedAttachment]:
        rows = c.session.execute(
            select(m.Attachment).where(m.Attachment.run_id == c.run.id, m.Attachment.message_id == message_id)
            .order_by(m.Attachment.id)
        ).scalars()  # fmt: skip
        return [ParsedAttachment(a.filename, a.mime_type, self.blobs.get(a.blob_ref), a.inline,
                                 a.content_id) for a in rows]  # fmt: skip

    def forward(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                to: list[str], text: str = "", cc: list[str] | None = None,
                actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        def fn(c: Ctx) -> dict[str, Any]:
            orig = self._message(c, mailbox_id, message_id)
            subj = (
                orig.subject
                if orig.subject.lower().startswith(("fwd:", "fw:"))
                else f"Fwd: {orig.subject}"
            )
            quoted = (f"{text}\n\n---------- Forwarded message ----------\n"
                      f"From: {orig.from_['email']}\nSubject: {orig.subject}\n\n{orig.body_text or ''}")  # fmt: skip
            return self._send(c, mailbox_id, to, list(cc or []), [], subj, quoted, None, None,
                              [*orig.references, orig.message_id_header], actor, "forward",
                              attachments=self._blob_attachments(c, orig.id))  # fmt: skip

        input_: dict[str, Any] = {"message_id": message_id, "to": to, "body_chars": len(text)}
        if cc:
            input_["cc"] = list(cc)
        return self._command(scope, run_id, actor, "forward", input_, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def insert_message(self, scope: Scope, run_id: str, mailbox_id: str, raw: bytes, *,
                       folder: str = "inbox", flags: Sequence[str] = (),
                       labels: Sequence[str] = (), actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Place a message into the mailbox WITHOUT sending it (Gmail `insert` semantics),
        with its initial `flags` and `labels` (Gmail insert labelIds, IMAP APPEND flags and
        keywords) in the SAME `insert` command: all of it is stored or none of it (a refused
        flag or label, the per-message label cap, the budget or the event cap)."""
        flags, labels = sorted(set(flags)), sorted(set(labels))

        def fn(c: Ctx) -> dict[str, Any]:
            target = self._resolve_folder(c, mailbox_id, folder)
            if (
                target.system_type is None
            ):  # pragma: no cover - all folders are system folders today
                raise UnsupportedOperation("insert into user folders is not supported")
            if actor == AGENT and target.system_type in ("sent", "drafts"):
                # like a move or copy there (_check_placement): Sent and Drafts hold only what
                # this account actually sent or drafted; an inserted message is neither
                raise PermissionDenied(
                    f"messages cannot be added to {target.name}: it holds only messages this "
                    f"account {'sent' if target.system_type == 'sent' else 'drafted'}",
                    details={"folder": target.system_type},
                )
            bad = [f for f in flags if f not in VALID_FLAGS]
            if bad:
                raise InvalidRequest(f"unknown flag(s): {bad}")
            if any(not x.strip() for x in labels):
                raise InvalidRequest("label names must not be empty")
            if actor == AGENT:
                error = draft_flag_error(False, flags, ())  # an inserted message is no draft
                if error is not None:
                    raise error
                refuse_system_labels(labels)
                limit = run_cap(c.config, "max_labels_per_message")
                if len(labels) > limit:
                    raise labels_per_message_exceeded(limit)
            mid = self._insert_raw(c, mailbox_id, raw, event_type="message.inserted",
                                   folder_type=target.system_type, received_at=c.now,
                                   actor=actor, operation="insert")  # fmt: skip
            for event_type, names, op in (("message.flags_changed", flags, "flag"),
                                          ("message.labels_changed", labels, "label")):  # fmt: skip
                if names:
                    c.emit(event_type, {"mailbox_id": mailbox_id, "message_id": mid,
                                        "add": list(names), "remove": []},
                           actor=actor, operation="insert")  # fmt: skip
                    c.also_triggers.append(op)  # as the separate flag/label write did
            return {"message_id": mid}

        input_: dict[str, Any] = {"bytes": len(raw), "folder": folder}
        if flags or labels:
            input_ |= {"flags": flags, "labels": labels}
        return self._command(scope, run_id, actor, "insert", input_, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def _draft(self, c: Ctx, mailbox_id: str, message_id: str) -> m.Message:
        row = self._message(c, mailbox_id, message_id)
        if not row.is_draft:
            raise Conflict(f"message {message_id} is not a draft")
        return row

    def update_draft(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                     to: list[str], subject: str, text: str = "", cc: list[str] | None = None,
                     bcc: list[str] | None = None, html: str | None = None,
                     keep_attachments: bool = True, threading: DraftThreading | None = None,
                     actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Replace a draft's content. The draft id is stable; the message id changes (as in
        Gmail). By default reply headers, the reply/forward origin (and attachments) carry
        over, so threading holds; `threading` replaces them instead (a client that rewrites
        the whole message, e.g. Gmail drafts.update with new In-Reply-To / References)."""

        def fn(c: Ctx) -> dict[str, Any]:
            old = self._draft(c, mailbox_id, message_id)
            draft_id = old.provider_ids.get("draft_id") or old.id
            content = DraftContent(
                to,
                cc or [],
                bcc or [],
                subject,
                text,
                html,
                old.in_reply_to,
                list(old.references),
                self._blob_attachments(c, old.id) if keep_attachments else [],
                old.provider_ids.get("origin"),
            )
            if threading is not None:
                content.in_reply_to = threading.in_reply_to
                content.references = list(threading.references)
                content.origin = None
                if threading.reply_to is not None:
                    self._answer(c, mailbox_id, content, threading.reply_to, "reply")
            self._emit_delete(c, old, actor, "draft")
            return {"message_id": self._insert_draft(c, mailbox_id, content, draft_id, actor),
                    "draft_id": draft_id}  # fmt: skip

        return self._command(scope, run_id, actor, "draft",
                             {"message_id": message_id, "to": to, "subject_chars": len(subject)}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    @staticmethod
    def _draft_operation(session: Session, run_id: str, mailbox_id: str,
                         draft: m.Message | None) -> tuple[str, m.Message | None]:  # fmt: skip
        """`send`, or `reply` / `forward` when the draft answers a message still in the
        mailbox (then also that original)."""
        origin = (
            draft.provider_ids.get("origin")
            if draft is not None and draft.is_draft and draft.mailbox_id == mailbox_id
            else None
        )  # fmt: skip (never reveals anything about another mailbox)
        if not isinstance(origin, dict) or origin.get("kind") not in ("reply", "forward"):
            return "send", None
        orig = session.get(m.Message, (str(origin.get("message_id")), run_id))
        if orig is None or orig.mailbox_id != mailbox_id or orig.is_draft:
            return "send", None  # the original is gone: an ordinary send of the draft
        return str(origin["kind"]), orig

    def send_draft(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                   actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Draft -> sent: the draft disappears, a Sent copy appears, recipients receive it.

        A draft that answers a message (create_draft reply_to / forward_of) is sent as ONE
        `reply` / `forward` operation: permission, budget, triggers and policies see that
        operation, and a reply marks the original ANSWERED, exactly like the direct command.
        A reply draft that carries attachments additionally needs `send` or `forward`
        permission (a reply alone never transmits files)."""
        with self._sf() as s:  # decide the operation the guard checks (re-verified under lock)
            run = get_run(s, scope, run_id)
            op, first = self._draft_operation(s, run.id, mailbox_id,
                                              s.get(m.Message, (message_id, run.id)))  # fmt: skip
            orig_id = first.id if first is not None else None

        def fn(c: Ctx) -> dict[str, Any]:
            d = self._draft(c, mailbox_id, message_id)
            now_op, orig = self._draft_operation(c.session, c.run.id, mailbox_id, d)
            if now_op != op:  # pragma: no cover - the draft's original vanished concurrently
                raise Conflict("the draft changed while it was being sent; send it again")
            to, cc, bcc = (
                [a["email"] for a in d.to],
                [a["email"] for a in d.cc],
                [a["email"] for a in d.bcc],
            )
            subject, text, html, irt, refs = (
                d.subject,
                d.body_text or "",
                d.body_html,
                d.in_reply_to,
                list(d.references),
            )
            atts = self._blob_attachments(c, d.id)
            if (
                op == "reply"
                and atts
                and actor == AGENT
                and not (self._permitted(c, "send") or self._permitted(c, "forward"))
            ):
                raise PermissionDenied("a reply carrying attachments also needs the 'send' or "
                                       "'forward' operation, which is not permitted")  # fmt: skip
            self._emit_delete(c, d, actor, op)
            result = self._send(c, mailbox_id, to, cc, bcc, subject, text, html, irt,
                                refs or None, actor, op, attachments=atts)  # fmt: skip
            own = [self._mailbox(c, mailbox_id).email]
            if op == "reply" and orig is not None and not is_self_addressed([*to, *cc, *bcc], own):
                c.emit("message.flags_changed", {"mailbox_id": mailbox_id, "message_id": orig.id, "add": ["ANSWERED"], "remove": []},
                       actor=actor, operation="reply")  # fmt: skip
            return result

        input_: dict[str, Any] = {"draft_message_id": message_id}
        if op != "send":  # like the direct reply / forward: the answered message's id
            input_.update(message_id=orig_id, draft_of=op)
        return self._command(scope, run_id, actor, op, input_, fn, mailbox_id=mailbox_id)

    def copy(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, folder: str, *,
             actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """IMAP COPY / Graph copy: a new message (new id) with identical content in `folder`."""

        def fn(c: Ctx) -> dict[str, Any]:
            row = self._message(c, mailbox_id, message_id)
            target = self._resolve_folder(c, mailbox_id, folder)
            self._check_placement(c, row, target, actor)
            atts = c.session.execute(
                select(m.Attachment).where(m.Attachment.run_id == c.run.id, m.Attachment.message_id == row.id)
                .order_by(m.Attachment.id)
            ).scalars().all()  # fmt: skip
            new_id = c.gen.next("msg")
            payload: dict[str, Any] = {
                "message": {
                    "id": new_id, "mailbox_id": mailbox_id, "message_id_header": row.message_id_header,
                    "thread_id": row.thread_id, "folder_id": target.id, "from": row.from_, "to": row.to,
                    "cc": row.cc, "bcc": row.bcc, "subject": row.subject, "body_text": row.body_text,
                    "body_html": row.body_html, "in_reply_to": row.in_reply_to,
                    "references": row.references, "created_at": iso(row.created_at),
                    "received_at": iso(row.received_at) if row.received_at else None,
                    "labels": list(row.labels), "flags": list(row.flags), "headers": row.headers,
                    "is_draft": row.is_draft, "raw_blob_ref": row.raw_blob_ref,
                    "attachments": [
                        {"id": c.gen.next("att"), "filename": a.filename, "mime_type": a.mime_type,
                         "size": a.size, "checksum": a.checksum, "blob_ref": a.blob_ref,
                         "inline": a.inline, "content_id": a.content_id} for a in atts
                    ],
                    "provider_ids": {},
                },
                "new_thread": None,
                "merged_threads": [],
            }  # fmt: skip
            c.emit("message.copied", payload, actor=actor, operation="copy")
            return {"message_id": new_id}

        return self._command(scope, run_id, actor, "copy", {"message_id": message_id, "folder": folder},
                             fn, mailbox_id=mailbox_id)  # fmt: skip

    def trash(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
              actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            target = self._folder(c, mailbox_id, "trash")
            if row.folder_id != target.id:
                c.emit("message.moved", {"mailbox_id": mailbox_id, "message_id": message_id,
                                         "folder_id": target.id}, actor=actor, operation="move")  # fmt: skip

        self._command(scope, run_id, actor, "move", {"message_id": message_id, "folder": "trash"}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def untrash(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                actor: str = AGENT) -> None:  # fmt: skip
        """Restore a trashed message to the folder it came from (inbox if unknown). No-op otherwise."""

        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            trash = self._folder(c, mailbox_id, "trash")
            if row.folder_id != trash.id:
                return
            dest = row.previous_folder_id or self._folder(c, mailbox_id, "inbox").id
            c.emit("message.moved", {"mailbox_id": mailbox_id, "message_id": message_id,
                                     "folder_id": dest}, actor=actor, operation="move")  # fmt: skip

        self._command(scope, run_id, actor, "move", {"message_id": message_id, "folder": "untrash"}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def expunge(self, scope: Scope, run_id: str, mailbox_id: str, folder: str, *,
                actor: str = AGENT) -> list[str]:  # fmt: skip
        """Permanently remove messages flagged DELETED in `folder` (IMAP EXPUNGE)."""

        def fn(c: Ctx) -> list[str]:
            target = self._resolve_folder(c, mailbox_id, folder)
            rows = c.session.execute(
                select(m.Message).where(m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                                        m.Message.folder_id == target.id,
                                        m.Message.flags.contains(["DELETED"]))
                .order_by(m.Message.created_at, m.Message.id)
            ).scalars().all()  # fmt: skip
            for row in rows:
                self._emit_delete(c, row, actor, "delete")
            return [r.id for r in rows]

        return self._command(scope, run_id, actor, "delete", {"folder": folder}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def complete_run(self, scope: Scope, run_id: str, verdict: str, *, evaluation_id: str,
                     evaluated_through_sequence: int, actor: str = EVALUATOR) -> None:  # fmt: skip
        """RUNNING -> PASSED/FAILED after an evaluation of the log up to a pinned sequence.

        CONFLICT if anything happened after the evaluated sequence (an agent action that was
        never graded). The event carries only the verdict and evaluation id: grading details
        stay in the evaluations table, never in the event log.
        """
        if verdict not in (RunStatus.PASSED, RunStatus.FAILED):
            raise InvalidRequest("verdict must be PASSED or FAILED")

        def fn(c: Ctx) -> None:
            if c.run.next_seq - 1 != evaluated_through_sequence:
                raise Conflict(
                    "the run changed after it was evaluated; re-evaluate before completing",
                    details={"evaluated_through": evaluated_through_sequence,
                             "latest": c.run.next_seq - 1},
                )  # fmt: skip
            self._status(c, RunStatus(verdict))
            c.emit("run.completed", {"verdict": verdict, "evaluation_id": evaluation_id},
                   actor=actor, operation="complete")  # fmt: skip

        self._command(
            scope, run_id, actor, "complete", {"verdict": verdict}, fn, require_running=True
        )

    def answer_key_for_run(
        self, scope: Scope, run_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """Evaluator-only: the hidden answer key (latest compatible, or `version`)."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            sv = s.get(m.ScenarioVersion, run.scenario_version_id)
            assert sv is not None
            key = sv.spec["scenario"]["id"]
            row = answer_key_for(s, scope, key, sv.version, version)
            return {"id": row.id, "version": row.version, "spec_hash": row.spec_hash,
                    "spec": row.spec, "source": row.source}  # fmt: skip

    def state_at(self, scope: Scope, run_id: str, up_to_sequence: int) -> dict[str, Any]:
        """Evaluator-only: state as it was right after event `up_to_sequence`.

        Rebuilt by replaying the recorded log into a throw-away run inside a transaction
        that is always rolled back, so nothing is persisted.
        """
        s = self._sf()
        try:
            orig = get_run(s, scope, run_id)
            if up_to_sequence < 1 or up_to_sequence >= orig.next_seq:
                raise InvalidRequest("up_to_sequence is outside the run's event log")
            events = s.execute(
                select(m.EventRow).where(m.EventRow.run_id == orig.id, m.EventRow.sequence <= up_to_sequence)
                .order_by(m.EventRow.sequence)
            ).scalars().all()  # fmt: skip
            tmp = m.Run(id=new_control_id("run"), tenant_id=orig.tenant_id, project_id=orig.project_id,
                        scenario_version_id=orig.scenario_version_id, status=str(RunStatus.DRAFT),
                        seed=orig.seed, config=orig.config, next_seq=1, replay_of=orig.id)  # fmt: skip
            s.add(tmp)
            s.flush()
            for ev in events:
                append(s, tmp, Event(ev.type, ev.payload, actor=ev.actor, operation=ev.operation,
                                     outcome=ev.outcome, baseline=ev.baseline,
                                     virtual_time=ev.virtual_time, id_counter_after=ev.id_counter_after))  # fmt: skip
            return snapshot(s, tmp.id)
        finally:
            s.rollback()
            s.close()

    def scenario_spec(self, scope: Scope, run_id: str) -> dict[str, Any]:
        """Evaluator-only: the scenario version this run was created from."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            if run.scenario_version_id is None:  # pragma: no cover - runs always have one
                raise NotFound("run has no scenario version")
            row = s.get(m.ScenarioVersion, run.scenario_version_id)
            assert row is not None
            return {"scenario_version_id": row.id, "version": row.version, "spec": row.spec}

    # ----------------------------------------------------------------- mailbox: reads

    def _canonical(self, c: Ctx, row: m.Message) -> CanonicalMessage:
        folder = c.session.get(m.Folder, (row.folder_id, c.run.id))
        assert folder is not None
        atts = c.session.execute(
            select(m.Attachment).where(m.Attachment.run_id == c.run.id, m.Attachment.message_id == row.id)
            .order_by(m.Attachment.id)
        ).scalars()  # fmt: skip
        labels = [folder.system_type.upper()] if folder.system_type else []
        labels += list(row.labels)
        if "SEEN" not in row.flags and not row.is_draft:
            labels.append("UNREAD")
        return CanonicalMessage(
            id=row.id, mailbox_id=row.mailbox_id, message_id_header=row.message_id_header,
            thread_id=row.thread_id, folder=folder.name, from_=Address(**row.from_),
            to=[Address(**a) for a in row.to], cc=[Address(**a) for a in row.cc],
            bcc=[Address(**a) for a in row.bcc], subject=row.subject, body_text=row.body_text,
            body_html=row.body_html, in_reply_to=row.in_reply_to, references=list(row.references),
            created_at=row.created_at, received_at=row.received_at, labels=labels,
            flags=list(row.flags),
            attachments=[
                AttachmentRef(id=a.id, filename=a.filename, mime_type=a.mime_type, size=a.size,
                              checksum=a.checksum, blob_ref=a.blob_ref, inline=a.inline,
                              content_id=a.content_id) for a in atts
            ],
            headers=row.headers, is_draft=row.is_draft, raw_blob_ref=row.raw_blob_ref,
            imap_uid=row.imap_uid, provider_ids=self._provider_ids(c, row),
        )  # fmt: skip

    def _provider_ids(self, c: Ctx, row: m.Message) -> dict[str, str]:
        pid = m.ProviderId
        ids = {f"{p}_{t}_id": x for p, t, x in c.session.execute(
            select(pid.provider, pid.entity_type, pid.external_id)
            .where(pid.run_id == c.run.id, pid.entity_id.in_([row.id, row.thread_id]))
        )}  # fmt: skip
        if row.provider_ids.get("draft_id"):
            ids["draft_id"] = row.provider_ids["draft_id"]
        return dict(sorted(ids.items()))

    def list_messages(self, scope: Scope, run_id: str, mailbox_id: str, *, folder: str | None = None,
                      actor: str = AGENT) -> list[CanonicalMessage]:  # fmt: skip
        def fn(c: Ctx) -> list[CanonicalMessage]:
            self._mailbox(c, mailbox_id)
            stmt = select(m.Message).where(
                m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id
            )
            if folder is not None:
                stmt = stmt.where(
                    m.Message.folder_id == self._resolve_folder(c, mailbox_id, folder).id
                )
            rows = c.session.execute(stmt.order_by(m.Message.created_at, m.Message.id)).scalars()
            return [self._canonical(c, r) for r in rows]

        return self._read(scope, run_id, actor, "read", {"folder": folder}, fn, mailbox_id)

    def list_messages_page(self, scope: Scope, run_id: str, mailbox_id: str, *,
                           folder: str | None = None, page_size: int = 50, cursor: str | None = None,
                           order: str = "asc", actor: str = AGENT) -> Page[CanonicalMessage]:  # fmt: skip
        """Keyset-paginated listing ordered by (created_at, id): stable under a fixed state."""
        check_page_size(page_size)
        if order not in ("asc", "desc"):
            raise InvalidRequest("order must be 'asc' or 'desc'")
        after = decode_cursor(cursor) if cursor else None

        def fn(c: Ctx) -> Page[CanonicalMessage]:
            return self._message_page(c, mailbox_id, folder, page_size, after, order)

        return self._read(scope, run_id, actor, "read",
                          {"folder": folder, "page_size": page_size, "paged": cursor is not None},
                          fn, mailbox_id)  # fmt: skip

    def _message_page(self, c: Ctx, mailbox_id: str, folder: str | None, page_size: int,
                      after: tuple[str, str] | None, order: str) -> Page[CanonicalMessage]:  # fmt: skip
        self._mailbox(c, mailbox_id)
        stmt = select(m.Message).where(
            m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id
        )
        if folder is not None:
            stmt = stmt.where(m.Message.folder_id == self._resolve_folder(c, mailbox_id, folder).id)
        key = tuple_(m.Message.created_at, m.Message.id)
        if after is not None:
            bound = (parse_ts(after[0]), after[1])
            stmt = stmt.where(key > bound if order == "asc" else key < bound)
        ordering = (
            (m.Message.created_at, m.Message.id)
            if order == "asc"
            else (m.Message.created_at.desc(), m.Message.id.desc())
        )
        rows = c.session.execute(stmt.order_by(*ordering).limit(page_size + 1)).scalars().all()
        items = [self._canonical(c, r) for r in rows[:page_size]]
        nxt = None
        if len(rows) > page_size:
            last = rows[page_size - 1]
            nxt = encode_cursor(iso(last.created_at), last.id)
        return Page(items, nxt)

    def inspect_messages(self, scope: Scope, run_id: str, mailbox_id: str, *,
                         folder: str | None = None, page_size: int = 50, cursor: str | None = None,
                         order: str = "asc") -> Page[CanonicalMessage]:  # fmt: skip
        """Operator (controller/evaluator) read of any mailbox in the run. Not an agent
        action: it records nothing, arms no triggers and works on finished runs too."""
        check_page_size(page_size)
        if order not in ("asc", "desc"):
            raise InvalidRequest("order must be 'asc' or 'desc'")
        after = decode_cursor(cursor) if cursor else None
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            return self._message_page(Ctx(s, run, id_generator(run)), mailbox_id, folder,
                                      page_size, after, order)  # fmt: skip

    def list_mailboxes(self, scope: Scope, run_id: str) -> list[dict[str, Any]]:
        """Mailboxes with their persona and per-folder message / unread counts (aggregates)."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            rows = s.execute(
                select(m.Mailbox).where(m.Mailbox.run_id == run_id).order_by(m.Mailbox.owner_id)
            ).scalars()
            sv = s.get(m.ScenarioVersion, run.scenario_version_id)
            personas = {p["id"]: p for p in (sv.spec["scenario"]["personas"] if sv else [])}
            agent = run.config.get("agent", {}).get("mailbox_owner")
            folders: dict[str, list[dict[str, Any]]] = {}
            for f in s.execute(
                select(m.Folder).where(m.Folder.run_id == run_id).order_by(m.Folder.id)
            ).scalars():
                folders.setdefault(f.mailbox_id, []).append(
                    {"id": f.id, "name": f.name, "system_type": f.system_type, "total": 0,
                     "unread": 0}
                )  # fmt: skip
            unread = func.count().filter(~(literal("SEEN") == any_(m.Message.flags)))
            counts = {
                fid: (total, n_unread)
                for fid, total, n_unread in s.execute(
                    select(m.Message.folder_id, func.count(), unread)
                    .where(m.Message.run_id == run_id)
                    .group_by(m.Message.folder_id)
                ).all()
            }
            for entries in folders.values():
                for entry in entries:
                    entry["total"], entry["unread"] = counts.get(entry["id"], (0, 0))
            out = []
            for r in rows:
                persona = personas.get(r.owner_id, {})
                out.append({"id": r.id, "owner_id": r.owner_id, "email": r.email,
                            "persona_id": r.owner_id, "name": persona.get("name"),
                            "role": persona.get("role"), "is_agent": r.owner_id == agent,
                            "folders": folders.get(r.id, [])})  # fmt: skip
            return out

    def events_page(self, scope: Scope, run_id: str, *, after_sequence: int = 0, limit: int = 100,
                    event_type: str | None = None, actor: str | None = None,
                    epoch: int | None = None) -> tuple[list[dict[str, Any]], int | None]:  # fmt: skip
        """Filtered, keyset-paginated event trace. Returns (items, next after_sequence|None)."""
        check_page_size(limit)
        ev = m.EventRow
        stmt = select(ev).where(ev.run_id == run_id, ev.sequence > after_sequence)
        if event_type is not None:
            stmt = stmt.where(ev.type == event_type)
        if actor is not None:
            stmt = stmt.where(ev.actor == actor)
        if epoch is not None:
            stmt = stmt.where(ev.epoch == epoch)
        with self._sf() as s:
            get_run(s, scope, run_id)
            rows = s.execute(stmt.order_by(ev.sequence).limit(limit + 1)).scalars().all()
        items: list[dict[str, Any]] = [
            {"sequence": r.sequence, "epoch": r.epoch, "type": r.type, "actor": r.actor,
             "operation": r.operation, "outcome": r.outcome, "payload": r.payload,
             "virtual_time": iso(r.virtual_time) if r.virtual_time else None}
            for r in rows[:limit]
        ]  # fmt: skip
        nxt: int | None = rows[limit - 1].sequence if len(rows) > limit else None
        return items, nxt

    def list_changes(self, scope: Scope, run_id: str, mailbox_id: str, since_sequence: int, *,
                     limit: int = 500, actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Mailbox change feed keyed by event sequence (backs Gmail history / Graph delta).

        `reset_since` is true when the run was reset after `since_sequence`: callers must then
        resynchronise fully (Gmail answers 404 for a stale historyId in that case).
        """
        check_page_size(limit)
        if since_sequence < 0:
            raise InvalidRequest("since_sequence must be >= 0")

        def fn(c: Ctx) -> dict[str, Any]:
            self._mailbox(c, mailbox_id)
            ev = m.EventRow
            reset_since = c.session.scalar(
                select(func.count()).select_from(ev).where(
                    ev.run_id == c.run.id, ev.sequence > since_sequence, ev.type == "run.reset")
            )  # fmt: skip
            rows = c.session.execute(
                select(ev).where(
                    ev.run_id == c.run.id, ev.sequence > since_sequence, ev.epoch == c.run.epoch,
                    (ev.payload["mailbox_id"].astext == mailbox_id)
                    | (ev.payload["message"]["mailbox_id"].astext == mailbox_id),
                ).order_by(ev.sequence).limit(limit + 1)
            ).scalars().all()  # fmt: skip
            has_more = len(rows) > limit
            rows = rows[:limit]
            changes = [
                {"sequence": r.sequence, "type": r.type,
                 "message_id": (r.payload.get("message") or {}).get("id") or r.payload.get("message_id")}
                for r in rows
            ]  # fmt: skip
            # Resume from `next_since`, never from latest_sequence: when the page was cut short
            # the remaining changes lie between the two.
            next_since = rows[-1].sequence if has_more else c.run.next_seq - 1
            return {"changes": changes, "next_since": next_since, "has_more": has_more,
                    "latest_sequence": c.run.next_seq - 1, "reset_since": bool(reset_since)}  # fmt: skip

        return self._read(scope, run_id, actor, "read", {"since_sequence": since_sequence}, fn,
                          mailbox_id)  # fmt: skip

    def resolve_external_id(self, scope: Scope, run_id: str, mailbox_id: str, provider: str,
                            entity_type: str | Sequence[str], external_id: str, *,
                            actor: str = AGENT) -> str:  # fmt: skip
        """Provider id -> internal id, only for entities inside `mailbox_id` (else NOT_FOUND).

        `entity_type` may list several kinds tried in order (e.g. a Graph id naming a message
        OR a draft): ONE recorded read, which fails only when none of them matches."""
        kinds = (entity_type,) if isinstance(entity_type, str) else tuple(entity_type)
        if not kinds or any(k not in ("message", "thread", "draft") for k in kinds):
            raise InvalidRequest("entity_type must be message, thread or draft")

        def fn(c: Ctx) -> str:
            self._mailbox(c, mailbox_id)
            pid = m.ProviderId
            for kind in kinds:
                entity = c.session.scalar(
                    select(pid.entity_id).where(pid.run_id == c.run.id, pid.provider == provider,
                                                pid.entity_type == kind, pid.external_id == external_id)
                )  # fmt: skip
                owner: m.Thread | m.Message | None = None
                if entity:
                    owner = (c.session.get(m.Thread, (entity, c.run.id)) if kind == "thread"
                             else c.session.get(m.Message, (entity, c.run.id)))  # fmt: skip
                if owner is not None and owner.mailbox_id == mailbox_id:
                    return str(entity)
            raise NotFound(f"{provider} {'/'.join(kinds)} {external_id} not found")

        return self._read(scope, run_id, actor, "read",
                          {"provider": provider, "entity_type": "|".join(kinds)}, fn, mailbox_id)  # fmt: skip

    def present_messages(self, scope: Scope, run_id: str, mailbox_id: str,
                         message_ids: Sequence[str], *, actor: str = AGENT) -> dict[str, CanonicalMessage]:  # fmt: skip
        """The listed messages that still exist in this mailbox, keyed by id: ONE read that
        never fails for a missing id (change feeds look up messages that may be gone)."""
        wanted = list(dict.fromkeys(message_ids))

        def fn(c: Ctx) -> dict[str, CanonicalMessage]:
            self._mailbox(c, mailbox_id)
            found: dict[str, CanonicalMessage] = {}
            for at in range(0, len(wanted), PRESENT_CHUNK):  # bounded IN lists
                rows = c.session.execute(
                    select(m.Message).where(m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                                            m.Message.id.in_(wanted[at:at + PRESENT_CHUNK]))
                ).scalars().all()  # fmt: skip
                found.update((r.id, self._canonical(c, r)) for r in rows)
            return {i: found[i] for i in wanted if i in found}

        if not wanted:
            return {}  # nothing to look up: no read is recorded
        return self._read(
            scope, run_id, actor, "read", {"message_ids": len(wanted)}, fn, mailbox_id
        )

    def get_message(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                    *, actor: str = AGENT) -> CanonicalMessage:  # fmt: skip
        return self._read(scope, run_id, actor, "read", {"message_id": message_id},
                          lambda c: self._canonical(c, self._message(c, mailbox_id, message_id)),
                          mailbox_id)  # fmt: skip

    def get_raw(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                *, actor: str = AGENT) -> bytes | None:  # fmt: skip
        """The stored RFC 5322 bytes of a message of this mailbox (None if none were kept)."""

        def fn(c: Ctx) -> bytes | None:
            row = self._message(c, mailbox_id, message_id)
            return self.blobs.get(row.raw_blob_ref) if row.raw_blob_ref else None

        return self._read(scope, run_id, actor, "read", {"message_id": message_id, "raw": True},
                          fn, mailbox_id)  # fmt: skip

    def _attachment(self, c: Ctx, mailbox_id: str, message_id: str, attachment_id: str,
                    max_bytes: int | None) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        if max_bytes is None:
            max_bytes = MAX_ATTACHMENT_READ_BYTES
        self._message(c, mailbox_id, message_id)  # mailbox-scoped: 404 for another mailbox's
        a = c.session.get(m.Attachment, (attachment_id, c.run.id))
        if a is None or a.message_id != message_id:
            raise NotFound(f"attachment {attachment_id} not found")
        if a.size > max_bytes:
            raise UnsupportedOperation(
                f"attachment is {a.size} bytes; this read returns at most {max_bytes} bytes",
                details={"size": a.size, "max_bytes": max_bytes},
            )
        ref = AttachmentRef(id=a.id, filename=a.filename, mime_type=a.mime_type, size=a.size,
                            checksum=a.checksum, blob_ref=a.blob_ref, inline=a.inline,
                            content_id=a.content_id)  # fmt: skip
        return ref, self.blobs.get(a.blob_ref)

    def get_attachment(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                       attachment_id: str, *, max_bytes: int | None = None,
                       actor: str = AGENT) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        """One attachment of a message of this mailbox: its metadata and bytes (a read).
        Larger than `max_bytes` -> UNSUPPORTED_OPERATION before the bytes are loaded."""
        return self._read(
            scope, run_id, actor, "read",
            {"message_id": message_id, "attachment_id": attachment_id},
            lambda c: self._attachment(c, mailbox_id, message_id, attachment_id, max_bytes),
            mailbox_id,
        )  # fmt: skip

    def inspect_attachment(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                           attachment_id: str, *,
                           max_bytes: int | None = None) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        """Operator read of an attachment of any mailbox in the run (records nothing)."""
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            c = Ctx(s, run, id_generator(run))
            self._mailbox(c, mailbox_id)
            return self._attachment(c, mailbox_id, message_id, attachment_id, max_bytes)

    def find_by_message_id(self, scope: Scope, run_id: str, mailbox_id: str, header: str,
                           *, actor: str = AGENT) -> list[CanonicalMessage]:  # fmt: skip
        """Non-draft messages of this mailbox whose Message-ID header is `header` (indexed)."""

        def fn(c: Ctx) -> list[CanonicalMessage]:
            self._mailbox(c, mailbox_id)
            rows = c.session.execute(
                select(m.Message).where(m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                                        m.Message.message_id_header == header,
                                        m.Message.is_draft.is_(False))
                .order_by(m.Message.created_at, m.Message.id)
            ).scalars().all()  # fmt: skip
            return [self._canonical(c, r) for r in rows]

        return self._read(scope, run_id, actor, "read", {"message_id_header": header[:256]}, fn,
                          mailbox_id)  # fmt: skip

    def mark_seen(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                  *, actor: str = AGENT) -> bool:  # fmt: skip
        """Set SEEN as a side effect of READING the message (IMAP non-PEEK FETCH, RFC 3501
        6.4.5): recorded as a `read` (allowed whenever reading is, never budgeted), unlike
        `set_flags`, which is the agent's explicit `flag` write. True if SEEN was added."""

        def fn(c: Ctx) -> bool:
            msg = self._canonical(c, self._message(c, mailbox_id, message_id))
            if "SEEN" in msg.flags:
                return False
            c.emit("message.flags_changed",
                   {"mailbox_id": mailbox_id, "message_id": message_id, "add": ["SEEN"], "remove": []},
                   actor=actor, operation="read")  # fmt: skip
            return True

        return self._read(scope, run_id, actor, "read", {"message_id": message_id, "mark_seen": True},
                          fn, mailbox_id)  # fmt: skip

    def get_thread(self, scope: Scope, run_id: str, mailbox_id: str, thread_id: str,
                   *, actor: str = AGENT) -> list[CanonicalMessage]:  # fmt: skip
        def fn(c: Ctx) -> list[CanonicalMessage]:
            self._mailbox(c, mailbox_id)
            rows = c.session.execute(
                select(m.Message).where(m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                                        m.Message.thread_id == thread_id)
                .order_by(m.Message.created_at, m.Message.id)
            ).scalars().all()  # fmt: skip
            if not rows:
                raise NotFound(f"thread {thread_id} not found")
            return [self._canonical(c, r) for r in rows]

        return self._read(scope, run_id, actor, "read", {"thread_id": thread_id}, fn, mailbox_id)

    def search_messages(self, scope: Scope, run_id: str, mailbox_id: str, query: str,
                        *, actor: str = AGENT) -> list[CanonicalMessage]:  # fmt: skip
        def fn(c: Ctx) -> list[CanonicalMessage]:
            self._mailbox(c, mailbox_id)
            needle = (
                "%"
                + query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            rows = c.session.execute(
                select(m.Message).where(
                    m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                    (func.lower(m.Message.subject).like(needle, escape="\\"))
                    | (func.lower(func.coalesce(m.Message.body_text, "")).like(needle, escape="\\"))
                    | (func.lower(m.Message.from_["email"].astext).like(needle, escape="\\")),
                ).order_by(m.Message.created_at, m.Message.id)
            ).scalars()  # fmt: skip
            return [self._canonical(c, r) for r in rows]

        if not query.strip():
            raise InvalidRequest("empty search query")
        return self._read(
            scope, run_id, actor, "search", {"query_chars": len(query)}, fn, mailbox_id
        )

    def list_folders(
        self, scope: Scope, run_id: str, mailbox_id: str, *, actor: str = AGENT
    ) -> list[dict[str, Any]]:
        def fn(c: Ctx) -> list[dict[str, Any]]:
            self._mailbox(c, mailbox_id)
            rows = c.session.execute(
                select(m.Folder).where(m.Folder.run_id == c.run.id, m.Folder.mailbox_id == mailbox_id)
                .order_by(m.Folder.id)
            ).scalars()  # fmt: skip
            order = {st: i for i, (_, st) in enumerate(SYSTEM_FOLDERS)}
            ordered = sorted(rows, key=lambda f: (order.get(f.system_type or "", 99), f.name))
            return [{"id": f.id, "name": f.name, "system_type": f.system_type,
                     "uid_validity": f.uid_validity, "uid_next": f.uid_next} for f in ordered]  # fmt: skip

        return self._read(scope, run_id, actor, "read", {}, fn, mailbox_id)

    def mailbox_info(
        self, scope: Scope, run_id: str, mailbox_id: str, *, actor: str = AGENT
    ) -> dict[str, Any]:
        """The mailbox's own identity and the run's virtual time (adapters: /me, profile)."""

        def fn(c: Ctx) -> dict[str, Any]:
            mb = self._mailbox(c, mailbox_id)
            return {"mailbox_id": mb.id, "email": mb.email, "run_id": c.run.id,
                    "virtual_time": iso(c.now) if c.run.virtual_time is not None else None}  # fmt: skip

        return self._read(scope, run_id, actor, "read", {}, fn, mailbox_id)

    def _read(self, scope: Scope, run_id: str, actor: str, op: str, input_: dict[str, Any],
              fn: Callable[[Ctx], T], mailbox_id: str) -> T:  # fmt: skip
        return self._command(scope, run_id, actor, op, input_, fn,
                             require_running=actor == AGENT, mailbox_id=mailbox_id)  # fmt: skip


__all__ = [
    "AGENT",
    "CONTROLLER",
    "EVALUATOR",
    "MAX_ADVANCE_SECONDS",
    "MAX_ATTACHMENT_READ_BYTES",
    "MAX_RECIPIENTS",
    "labels_per_message_exceeded",
    "SIMULATOR",
    "DraftThreading",
    "ReplayResult",
    "SimEngine",
    "TERMINAL",
]
