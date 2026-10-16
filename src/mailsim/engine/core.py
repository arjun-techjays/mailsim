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
from datetime import datetime, timedelta
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
)
from mailsim.domain.states import TERMINAL, RunStatus, check_transition
from mailsim.engine import projectors as _projectors  # noqa: F401  (registers projectors)
from mailsim.engine.context import current_interface
from mailsim.engine.delivery import DeliveryPolicy, ExternalMode
from mailsim.engine.log import Event, append, id_generator, lock_run, save_ids
from mailsim.engine.pagination import Page, check_page_size, decode_cursor, encode_cursor
from mailsim.engine.scheduler import Scheduled, due_in_order
from mailsim.engine.snapshot import snapshot, state_hash
from mailsim.mime.build import build_mime
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedAttachment, parse_mime
from mailsim.scenario.answer_keys import answer_key_for
from mailsim.scenario.schema import TRIGGER_ACTIONS, ScenarioBody, ScenarioDoc
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
# Budget rule (user decision): only WRITES consume the agent action budget. Reads and polling
# are free but still recorded as actions (efficiency metrics).
READ_OPERATIONS = frozenset({"read", "search"})


def counts_against_budget(operation: str) -> bool:
    return operation not in READ_OPERATIONS


@dataclass
class Ctx:
    session: Session
    run: m.Run
    gen: IdGenerator

    @property
    def now(self) -> datetime:
        assert self.run.virtual_time is not None
        return self.run.virtual_time

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


@dataclass
class ReplayResult:
    replay_run_id: str
    matches: bool
    original_hash: str
    replay_hash: str
    event_count: int
    missing_blobs: list[str]


def _addr(email: str) -> Address:
    email = email.strip().lower()
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
        try:
            with self._sf.begin() as s:
                run = lock_run(s, scope, run_id)
                seq_start = run.next_seq
                ctx = Ctx(s, run, id_generator(run))
                self._guard(ctx, actor, operation, mailbox_id, require_running)
                guarded = True
                try:
                    result = fn(ctx)
                except (ValueError, OverflowError, RecursionError) as exc:
                    # untyped failures (e.g. header injection) become audited INVALID_REQUESTs
                    raise InvalidRequest(f"invalid input: {exc}"[:300]) from exc
                if actor == AGENT:
                    if counts_against_budget(operation):
                        ctx.emit(
                            "agent.action_counted",
                            {"operation": operation, "outcome": "ok"},
                            actor=AGENT,
                            operation=operation,
                        )
                    self._arm_triggers(ctx, operation)
                if ctx.gen.counter != run.id_counter:
                    # ids were drawn after the last event: record it so replay stays exact
                    ctx.emit("ids.reserved", {"counter": ctx.gen.counter}, actor=actor,
                             operation=operation)  # fmt: skip
                s.add(
                    m.ActionRow(
                        run_id=run.id,
                        interface=current_interface.get(),
                        actor=actor,
                        operation=operation,
                        input=input_,
                        result=_summary(result),
                        outcome="ok",
                        virtual_time=run.virtual_time,
                        seq_start=seq_start,
                        seq_end=run.next_seq - 1,
                    )
                )
                return result
        except SimError as exc:
            # Budget rule: every agent command that passed the permission/mailbox/limit guard
            # consumes budget, whether it then succeeds or fails.
            self._record_denied(
                scope,
                run_id,
                actor,
                operation,
                input_,
                exc,
                count=guarded and actor == AGENT and counts_against_budget(operation),
            )
            raise

    def _record_denied(
        self,
        scope: Scope,
        run_id: str,
        actor: str,
        operation: str,
        input_: dict[str, Any],
        exc: SimError,
        count: bool = False,
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
            s.add(
                m.ActionRow(
                    run_id=run.id,
                    interface=current_interface.get(),
                    actor=actor,
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
        """Record a send attempt that an interface refused before it reached a command (SMTP
        `RCPT TO` outside the simulation): an EXTERNAL_DELIVERY_BLOCKED `send` action plus its
        `delivery.external_blocked` evidence event. Nothing is delivered; no budget is used."""
        exc = ExternalDeliveryBlocked(
            "recipient(s) outside the simulated environment; external delivery is not permitted",
            details={"recipients": [r.strip().lower() for r in recipients]},
        )
        with self._sf() as s:
            run = get_run(s, scope, run_id)
            mb = s.get(m.Mailbox, (mailbox_id, run.id))
            if mb is None or (
                actor == AGENT and mb.owner_id != run.config["agent"]["mailbox_owner"]
            ):
                raise NotFound(f"mailbox {mailbox_id} not found")
        self._record_denied(scope, run_id, actor, "send", {"to": exc.details["recipients"],
                                                           "stage": "envelope"}, exc)  # fmt: skip

    def _guard(
        self, ctx: Ctx, actor: str, operation: str, mailbox_id: str | None, require_running: bool
    ) -> None:
        run = ctx.run
        if require_running and run.status != RunStatus.RUNNING:
            raise RunNotActive(f"run is {run.status}, not RUNNING")
        if actor != AGENT:
            return
        agent = ctx.config["agent"]
        if operation in agent["prohibited"] or (
            agent["allowed"] and operation not in agent["allowed"]
        ):
            raise PermissionDenied(f"operation {operation!r} is not permitted for the agent")
        if mailbox_id is not None:
            mb = ctx.session.get(m.Mailbox, (mailbox_id, run.id))
            if mb is None:
                raise NotFound(f"mailbox {mailbox_id} not found")
            if mb.owner_id != agent["mailbox_owner"]:
                raise PermissionDenied("agent may only access its assigned mailbox")
        limit = ctx.config["limits"]["max_agent_actions"]
        if counts_against_budget(operation) and run.agent_actions >= limit:
            raise SimulationLimitExceeded(
                f"agent action limit of {limit} reached", details={"limit": limit}
            )

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
        if agent_build is not None and not (0 < len(agent_build) <= 200):
            raise InvalidRequest("agent_build must be 1-200 characters")
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
        result states whether the replayed state hash equals the original's.
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
                        from_=_addr(message["from"]),
                        to=[_addr(a) for a in message["to"]],
                        cc=[_addr(a) for a in message.get("cc", [])],
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

    def advance(self, scope: Scope, run_id: str, seconds: float) -> dict[str, Any]:
        return self._command(scope, run_id, CONTROLLER, "advance", {"seconds": seconds if math.isfinite(seconds) else str(seconds)},
                             lambda c: self._advance(c, seconds))  # fmt: skip

    def _advance(self, c: Ctx, seconds: float) -> dict[str, Any]:
        if not math.isfinite(seconds) or seconds < 0 or seconds > 10 * 365 * 86400:
            raise InvalidRequest("seconds must be a finite, non-negative duration")
        start = c.run.start_time
        assert start is not None
        target = c.now + timedelta(seconds=seconds)
        limit = c.config["limits"]["max_virtual_duration_seconds"]
        if (target - start).total_seconds() > limit:
            raise SimulationLimitExceeded(
                f"virtual duration limit of {limit}s exceeded", details={"limit": limit}
            )
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
            from_=Address(email=personas[spec["from"]]),
            to=[Address(email=personas[r]) for r in recipients],
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
                             from_=Address(email=persona.email), to=rcpts, subject=subject,
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
        policy = DeliveryPolicy(c.config["external_delivery"])
        plan = policy.plan([a.email for a in (*to_a, *cc_a, *bcc_a)], self._emails(c))
        if (
            actor == AGENT
            and plan.external
            and "external_delivery" in c.config["agent"]["prohibited"]
        ):
            raise ExternalDeliveryBlocked(
                "agent is prohibited from external delivery",
                details={"recipients": plan.external},
            )
        header = c.gen.message_id_header(c.config["domain"])
        raw = build_mime(
            message_id=header, from_=Address(email=mb.email), to=to_a, cc=cc_a, subject=subject,
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
        for ext in plan.external:
            if policy.mode is ExternalMode.SINK:
                c.emit("delivery.sunk",
                       {"id": c.gen.next("snk"), "recipient": ext, "raw_blob_ref": self.blobs.put(raw),
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
            from_=Address(email=mb.email), to=[_addr(a) for a in d.to], cc=[_addr(a) for a in d.cc],
            subject=d.subject, date=c.now, text=d.text, html=d.html, in_reply_to=d.in_reply_to,
            references=d.references or None, attachments=d.attachments or None,
        )  # fmt: skip
        return self._insert_raw(
            c, mailbox_id, raw, event_type="draft.created", folder_type="drafts",
            flags=["DRAFT", "SEEN"], received_at=None, bcc=[_addr(a) for a in d.bcc], is_draft=True,
            actor=actor, operation="draft", provider_ids={"draft_id": draft_id},
        )  # fmt: skip

    def create_draft(
        self, scope: Scope, run_id: str, mailbox_id: str, *, to: list[str], subject: str,
        text: str = "", cc: list[str] | None = None, bcc: list[str] | None = None,
        html: str | None = None, in_reply_to: str | None = None,
        references: list[str] | None = None, actor: str = AGENT,
    ) -> dict[str, Any]:  # fmt: skip
        content = DraftContent(
            to, cc or [], bcc or [], subject, text, html, in_reply_to, references or []
        )

        def fn(c: Ctx) -> dict[str, Any]:
            draft_id = c.gen.next("drf")
            return {"message_id": self._insert_draft(c, mailbox_id, content, draft_id, actor),
                    "draft_id": draft_id}  # fmt: skip

        return self._command(scope, run_id, actor, "draft", {"to": to, "subject_chars": len(subject)}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def move(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, folder: str,
             *, actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            self._message(c, mailbox_id, message_id)
            target = self._resolve_folder(c, mailbox_id, folder)
            c.emit("message.moved", {"mailbox_id": mailbox_id, "message_id": message_id, "folder_id": target.id},
                   actor=actor, operation="move")  # fmt: skip

        self._command(scope, run_id, actor, "move", {"message_id": message_id, "folder": folder},
                      fn, mailbox_id=mailbox_id)  # fmt: skip

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
            self._message(c, mailbox_id, message_id)
            if any(not x.strip() for x in (*add, *remove)):
                raise InvalidRequest("label names must not be empty")
            c.emit("message.labels_changed",
                   {"mailbox_id": mailbox_id, "message_id": message_id, "add": list(add), "remove": list(remove)},
                   actor=actor, operation="label")  # fmt: skip

        self._command(scope, run_id, actor, "label",
                      {"message_id": message_id, "add": list(add), "remove": list(remove)}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def set_flags(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                  add: Sequence[str] = (), remove: Sequence[str] = (), actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            self._message(c, mailbox_id, message_id)
            bad = [f for f in (*add, *remove) if f not in VALID_FLAGS]
            if bad:
                raise InvalidRequest(f"unknown flag(s): {bad}")
            c.emit("message.flags_changed",
                   {"mailbox_id": mailbox_id, "message_id": message_id, "add": list(add), "remove": list(remove)},
                   actor=actor, operation="flag")  # fmt: skip

        self._command(scope, run_id, actor, "flag",
                      {"message_id": message_id, "add": list(add), "remove": list(remove)}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

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
                to: list[str], text: str = "", actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        def fn(c: Ctx) -> dict[str, Any]:
            orig = self._message(c, mailbox_id, message_id)
            subj = (
                orig.subject
                if orig.subject.lower().startswith(("fwd:", "fw:"))
                else f"Fwd: {orig.subject}"
            )
            quoted = (f"{text}\n\n---------- Forwarded message ----------\n"
                      f"From: {orig.from_['email']}\nSubject: {orig.subject}\n\n{orig.body_text or ''}")  # fmt: skip
            return self._send(c, mailbox_id, to, [], [], subj, quoted, None, None,
                              [*orig.references, orig.message_id_header], actor, "forward",
                              attachments=self._blob_attachments(c, orig.id))  # fmt: skip

        return self._command(scope, run_id, actor, "forward",
                             {"message_id": message_id, "to": to, "body_chars": len(text)}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def insert_message(self, scope: Scope, run_id: str, mailbox_id: str, raw: bytes, *,
                       folder: str = "inbox", actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Place a message into the mailbox WITHOUT sending it (Gmail `insert` semantics)."""

        def fn(c: Ctx) -> dict[str, Any]:
            target = self._resolve_folder(c, mailbox_id, folder)
            if (
                target.system_type is None
            ):  # pragma: no cover - all folders are system folders today
                raise UnsupportedOperation("insert into user folders is not supported")
            mid = self._insert_raw(c, mailbox_id, raw, event_type="message.inserted",
                                   folder_type=target.system_type, received_at=c.now,
                                   actor=actor, operation="insert")  # fmt: skip
            return {"message_id": mid}

        return self._command(scope, run_id, actor, "insert", {"bytes": len(raw), "folder": folder},
                             fn, mailbox_id=mailbox_id)  # fmt: skip

    def _draft(self, c: Ctx, mailbox_id: str, message_id: str) -> m.Message:
        row = self._message(c, mailbox_id, message_id)
        if not row.is_draft:
            raise Conflict(f"message {message_id} is not a draft")
        return row

    def update_draft(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                     to: list[str], subject: str, text: str = "", cc: list[str] | None = None,
                     bcc: list[str] | None = None, html: str | None = None,
                     keep_attachments: bool = True, actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Replace a draft's content. The draft id is stable; the message id changes (as in
        Gmail). Reply headers (and by default attachments) carry over, so threading holds."""

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
            )
            self._emit_delete(c, old, actor, "draft")
            return {"message_id": self._insert_draft(c, mailbox_id, content, draft_id, actor),
                    "draft_id": draft_id}  # fmt: skip

        return self._command(scope, run_id, actor, "draft",
                             {"message_id": message_id, "to": to, "subject_chars": len(subject)}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def send_draft(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, *,
                   actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """Draft -> sent: the draft disappears, a Sent copy appears, recipients receive it."""

        def fn(c: Ctx) -> dict[str, Any]:
            d = self._draft(c, mailbox_id, message_id)
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
            self._emit_delete(c, d, actor, "send")
            return self._send(c, mailbox_id, to, cc, bcc, subject, text, html, irt, refs or None,
                              actor, "send", attachments=atts)  # fmt: skip

        return self._command(scope, run_id, actor, "send", {"draft_message_id": message_id}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def copy(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, folder: str, *,
             actor: str = AGENT) -> dict[str, Any]:  # fmt: skip
        """IMAP COPY / Graph copy: a new message (new id) with identical content in `folder`."""

        def fn(c: Ctx) -> dict[str, Any]:
            row = self._message(c, mailbox_id, message_id)
            target = self._resolve_folder(c, mailbox_id, folder)
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
                            entity_type: str, external_id: str, *, actor: str = AGENT) -> str:  # fmt: skip
        """Provider id -> internal id, only for entities inside `mailbox_id` (else NOT_FOUND)."""
        if entity_type not in ("message", "thread", "draft"):
            raise InvalidRequest("entity_type must be message, thread or draft")

        def fn(c: Ctx) -> str:
            self._mailbox(c, mailbox_id)
            pid = m.ProviderId
            entity = c.session.scalar(
                select(pid.entity_id).where(pid.run_id == c.run.id, pid.provider == provider,
                                            pid.entity_type == entity_type, pid.external_id == external_id)
            )  # fmt: skip
            owner: m.Thread | m.Message | None = None
            if entity:
                owner = (c.session.get(m.Thread, (entity, c.run.id)) if entity_type == "thread"
                         else c.session.get(m.Message, (entity, c.run.id)))  # fmt: skip
            if owner is None or owner.mailbox_id != mailbox_id:
                raise NotFound(f"{provider} {entity_type} {external_id} not found")
            return str(entity)

        return self._read(scope, run_id, actor, "read",
                          {"provider": provider, "entity_type": entity_type}, fn, mailbox_id)  # fmt: skip

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


__all__ = ["AGENT", "CONTROLLER", "EVALUATOR", "SIMULATOR", "ReplayResult", "SimEngine", "TERMINAL"]
