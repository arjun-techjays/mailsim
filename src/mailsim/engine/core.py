"""SimEngine: the canonical command surface over the event log.

Every public method is a command executed in one transaction under the run lock:
guard (run active, permission, limits) -> mutate via events -> record an audit Action.
Denied and failed commands are recorded as Actions too (in their own transaction).
Provider adapters (later phases) translate their native semantics into these commands.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import (
    Conflict,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    RunNotActive,
    SimError,
    SimulationLimitExceeded,
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
from mailsim.engine.delivery import DeliveryPolicy, ExternalMode
from mailsim.engine.log import Event, append, id_generator, lock_run, save_ids
from mailsim.engine.scheduler import Scheduled, due_in_order
from mailsim.engine.snapshot import snapshot, state_hash
from mailsim.mime.build import build_mime
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedAttachment, parse_mime
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
        return append(
            self.session,
            self.run,
            Event(type_, payload, actor=actor, operation=operation or type_, baseline=baseline),
        )


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
    if "@" not in email or " " in email:
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
        try:
            with self._sf.begin() as s:
                run = lock_run(s, scope, run_id)
                ctx = Ctx(s, run, id_generator(run))
                self._guard(ctx, actor, operation, mailbox_id, require_running)
                result = fn(ctx)
                if actor == AGENT:
                    run.agent_actions += 1
                    self._arm_triggers(ctx, operation)
                save_ids(run, ctx.gen)
                s.add(
                    m.ActionRow(
                        run_id=run.id,
                        actor=actor,
                        operation=operation,
                        input=input_,
                        result=_summary(result),
                        outcome="ok",
                        virtual_time=run.virtual_time,
                    )
                )
                return result
        except SimError as exc:
            self._record_denied(scope, run_id, actor, operation, input_, exc)
            raise

    def _record_denied(
        self,
        scope: Scope,
        run_id: str,
        actor: str,
        operation: str,
        input_: dict[str, Any],
        exc: SimError,
    ) -> None:
        with self._sf.begin() as s:
            try:
                run = get_run(s, scope, run_id)
            except NotFound:
                return
            s.add(
                m.ActionRow(
                    run_id=run.id,
                    actor=actor,
                    operation=operation,
                    input=input_,
                    result=exc.to_dict(),
                    outcome=str(exc.code),
                    virtual_time=run.virtual_time,
                )
            )

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
        if run.agent_actions >= limit:
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

    def create_run(self, scope: Scope, scenario_version_id: str) -> str:
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
                config=self._config(body),
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
                    actor=CONTROLLER,
                    operation="create_run",
                    input={"scenario_version_id": sv.id},
                    result={"status": run.status},
                    outcome="ok" if failure is None else str(failure.code),
                    virtual_time=run.virtual_time,
                )
            )
            run_id = run.id
        if failure is not None:
            raise failure
        return run_id

    @staticmethod
    def _config(body: ScenarioBody) -> dict[str, Any]:
        return {
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
            if c.run.status in (RunStatus.DRAFT, RunStatus.VALIDATED):
                raise Conflict(f"run is {c.run.status}; nothing to reset")
            baseline = (
                c.session.execute(
                    select(m.EventRow)
                    .where(m.EventRow.run_id == c.run.id, m.EventRow.baseline.is_(True))
                    .order_by(m.EventRow.sequence)
                )
                .scalars()
                .all()
            )
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
                          outcome=ev.outcome, baseline=ev.baseline, virtual_time=ev.virtual_time),
                )  # fmt: skip
                missing.extend(self._missing_blobs(ev.payload))
            replay_hash = state_hash(snapshot(s, replay.id))
            s.add(
                m.ActionRow(
                    run_id=orig.id,
                    actor=CONTROLLER,
                    operation="replay",
                    input={},
                    result={"replay_run_id": replay.id, "matches": original_hash == replay_hash},
                    outcome="ok",
                    virtual_time=orig.virtual_time,
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

    def actions(self, scope: Scope, run_id: str) -> list[dict[str, Any]]:
        with self._sf() as s:
            get_run(s, scope, run_id)
            rows = s.execute(
                select(m.ActionRow).where(m.ActionRow.run_id == run_id).order_by(m.ActionRow.id)
            ).scalars()
            return [
                {"actor": r.actor, "operation": r.operation, "input": r.input,
                 "result": r.result, "outcome": r.outcome}
                for r in rows
            ]  # fmt: skip

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
        return self._command(scope, run_id, CONTROLLER, "advance", {"seconds": seconds},
                             lambda c: self._advance(c, seconds))  # fmt: skip

    def _advance(self, c: Ctx, seconds: float) -> dict[str, Any]:
        if seconds < 0:
            raise InvalidRequest("clock cannot move backwards")
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
        recipients = [spec["to"]] if isinstance(spec["to"], str) else list(spec["to"])
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
            {"to": to, "cc": cc or [], "bcc": bcc or [], "subject": subject, "body_chars": len(text)},
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
        actor: str = AGENT,
    ) -> dict[str, Any]:
        def fn(c: Ctx) -> dict[str, Any]:
            mb = self._mailbox(c, mailbox_id)
            orig = self._message(c, mailbox_id, message_id)
            me = mb.email
            to = [orig.from_["email"]]
            cc: list[str] = []
            if reply_all:
                to += [a["email"] for a in orig.to if a["email"] != me]
                cc = [a["email"] for a in orig.cc if a["email"] != me]
            to = [a for a in dict.fromkeys(to) if a != me] or [orig.from_["email"]]
            subj = orig.subject if orig.subject.lower().startswith("re:") else f"Re: {orig.subject}"
            refs = [*orig.references, orig.message_id_header]
            result = self._send(c, mailbox_id, to, cc, [], subj, text, None,
                                orig.message_id_header, refs, actor, "reply")  # fmt: skip
            c.emit("message.flags_changed", {"message_id": orig.id, "add": ["ANSWERED"], "remove": []},
                   actor=actor, operation="reply")  # fmt: skip
            return result

        return self._command(
            scope, run_id, actor, "reply",
            {"message_id": message_id, "reply_all": reply_all, "body_chars": len(text)},
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
    ) -> dict[str, Any]:
        mb = self._mailbox(c, mailbox_id)
        to_a, cc_a, bcc_a = [_addr(a) for a in to], [_addr(a) for a in cc], [_addr(a) for a in bcc]
        if not (to_a or cc_a or bcc_a):
            raise InvalidRequest("at least one recipient is required")
        policy = DeliveryPolicy(c.config["external_delivery"])
        plan = policy.plan([a.email for a in (*to_a, *cc_a, *bcc_a)], self._emails(c))
        header = c.gen.message_id_header(c.config["domain"])
        raw = build_mime(
            message_id=header, from_=Address(email=mb.email), to=to_a, cc=cc_a, subject=subject,
            date=c.now, text=text, html=html, in_reply_to=in_reply_to, references=references,
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

    def create_draft(
        self, scope: Scope, run_id: str, mailbox_id: str, *, to: list[str], subject: str,
        text: str = "", actor: str = AGENT,
    ) -> dict[str, Any]:  # fmt: skip
        def fn(c: Ctx) -> dict[str, Any]:
            mb = self._mailbox(c, mailbox_id)
            raw = build_mime(
                message_id=c.gen.message_id_header(c.config["domain"]),
                from_=Address(email=mb.email), to=[_addr(a) for a in to], subject=subject,
                date=c.now, text=text,
            )  # fmt: skip
            mid = self._insert_raw(
                c, mailbox_id, raw, event_type="draft.created", folder_type="drafts",
                flags=["DRAFT", "SEEN"], received_at=None, is_draft=True, actor=actor, operation="draft",
            )  # fmt: skip
            return {"message_id": mid}

        return self._command(scope, run_id, actor, "draft", {"to": to, "subject": subject}, fn,
                             mailbox_id=mailbox_id)  # fmt: skip

    def move(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str, folder: str,
             *, actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            self._message(c, mailbox_id, message_id)
            target = self._resolve_folder(c, mailbox_id, folder)
            c.emit("message.moved", {"message_id": message_id, "folder_id": target.id},
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
                   {"message_id": message_id, "add": list(add), "remove": list(remove)},
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
                   {"message_id": message_id, "add": list(add), "remove": list(remove)},
                   actor=actor, operation="flag")  # fmt: skip

        self._command(scope, run_id, actor, "flag",
                      {"message_id": message_id, "add": list(add), "remove": list(remove)}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

    def delete(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
               *, actor: str = AGENT) -> None:  # fmt: skip
        def fn(c: Ctx) -> None:
            row = self._message(c, mailbox_id, message_id)
            remaining = c.session.scalar(
                select(func.count()).select_from(m.Message).where(
                    m.Message.run_id == c.run.id, m.Message.thread_id == row.thread_id,
                    m.Message.id != message_id,
                )
            )  # fmt: skip
            c.emit("message.deleted",
                   {"message_id": message_id, "empty_thread": None if remaining else row.thread_id},
                   actor=actor, operation="delete")  # fmt: skip

        self._command(scope, run_id, actor, "delete", {"message_id": message_id}, fn,
                      mailbox_id=mailbox_id)  # fmt: skip

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
        )  # fmt: skip

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

    def get_message(self, scope: Scope, run_id: str, mailbox_id: str, message_id: str,
                    *, actor: str = AGENT) -> CanonicalMessage:  # fmt: skip
        return self._read(scope, run_id, actor, "read", {"message_id": message_id},
                          lambda c: self._canonical(c, self._message(c, mailbox_id, message_id)),
                          mailbox_id)  # fmt: skip

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
            needle = f"%{query.lower()}%"
            rows = c.session.execute(
                select(m.Message).where(
                    m.Message.run_id == c.run.id, m.Message.mailbox_id == mailbox_id,
                    (func.lower(m.Message.subject).like(needle))
                    | (func.lower(func.coalesce(m.Message.body_text, "")).like(needle))
                    | (func.lower(m.Message.from_["email"].astext).like(needle)),
                ).order_by(m.Message.created_at, m.Message.id)
            ).scalars()  # fmt: skip
            return [self._canonical(c, r) for r in rows]

        if not query.strip():
            raise InvalidRequest("empty search query")
        return self._read(scope, run_id, actor, "search", {"query": query}, fn, mailbox_id)

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
            return [{"id": f.id, "name": f.name, "system_type": f.system_type} for f in ordered]

        return self._read(scope, run_id, actor, "read", {}, fn, mailbox_id)

    def _read(self, scope: Scope, run_id: str, actor: str, op: str, input_: dict[str, Any],
              fn: Callable[[Ctx], T], mailbox_id: str) -> T:  # fmt: skip
        return self._command(scope, run_id, actor, op, input_, fn,
                             require_running=actor == AGENT, mailbox_id=mailbox_id)  # fmt: skip


__all__ = ["AGENT", "CONTROLLER", "SIMULATOR", "ReplayResult", "SimEngine", "TERMINAL"]
