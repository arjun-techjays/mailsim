"""Control plane (spec 7.1) + answer keys + evaluations. Owner: WS-A (evaluations: WS-B).

Evaluation routes delegate to `app.state.evaluation_service` (frozen interface below, built by
WS-B). Without one, they answer 501 UNSUPPORTED_OPERATION.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session

from mailsim.api import authoring
from mailsim.api import schemas as s
from mailsim.api.auth import issue_key, project_keys, revoke_agent_keys, revoke_scoped_key
from mailsim.api.deps import Authenticated, Controller, Evaluator, Inspector, not_implemented
from mailsim.api.downloads import safe_download_headers
from mailsim.api.errors import ErrorEnvelope
from mailsim.api.idempotency import idempotent
from mailsim.api.pagination import decode_sequence_cursor, encode_sequence_cursor
from mailsim.api.routers._common import (
    blobs,
    controller,
    inspector,
    run_out,
    scenario_version,
    scope,
    sessions,
)
from mailsim.auth.principal import Principal, Role
from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import Conflict, InvalidRequest, NotFound
from mailsim.domain.states import TERMINAL, RunStatus
from mailsim.engine.facades import EvaluatorView
from mailsim.engine.pagination import decode_cursor, encode_cursor
from mailsim.evaluation.contract import BuildComparison, EvaluationReport
from mailsim.scenario import answer_keys as keys_service
from mailsim.scenario.answer_key import AnswerKeyDoc
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import models as m
from mailsim.store import repos

router = APIRouter(prefix="/v1", tags=["control"], responses={"default": {"model": ErrorEnvelope}})
Idem = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]
REPORT_TYPES = {"json": "application/json", "junit": "application/xml",
                "markdown": "text/markdown; charset=utf-8"}  # fmt: skip
REPORT_EXTENSIONS = {"json": "json", "junit": "xml", "markdown": "md"}


class EvaluationService(Protocol):
    """Frozen interface implemented by mailsim.evaluation.service.EvaluationService (WS-B)."""

    def evaluate(self, scope: repos.Scope, run_id: str, *, answer_key_version: int | None,
                 checks: str | None, complete_run: bool) -> EvaluationReport: ...  # fmt: skip
    def latest(self, scope: repos.Scope, run_id: str) -> EvaluationReport: ...
    def accuracy(self, scope: repos.Scope, scenario_key: str, *, answer_key_version: int | None,
                 builds: list[str] | None, baseline: str | None) -> BuildComparison: ...  # fmt: skip
    def report_blob(self, scope: repos.Scope, evaluation_id: str, fmt: str) -> bytes: ...
    def list(self, scope: repos.Scope, run_id: str) -> list[EvaluationReport]: ...


def _evaluations(request: Request) -> EvaluationService:
    service: EvaluationService | None = getattr(request.app.state, "evaluation_service", None)
    if service is None:
        raise not_implemented("WS-B")
    return service


def _payload(body: Any) -> Any:
    return body.model_dump(mode="json", by_alias=True)


# ---- projects --------------------------------------------------------------------------
def _project_out(row: m.Project) -> s.ProjectOut:
    return s.ProjectOut(id=row.id, tenant_id=row.tenant_id, name=row.name,
                        created_at=row.created_at)  # fmt: skip


@router.post("/projects", response_model=s.ProjectCreated, status_code=201)
def create_project(
    request: Request, body: s.ProjectCreate, principal: Controller
) -> s.ProjectCreated:
    """Create a project in the caller's TENANT (tenant-wide: any controller key of the tenant
    may do this). The response carries the new project's first controller and evaluator keys
    (shown once); the caller's own key stays bound to its own project. The very first
    tenant/project/keys come from `python -m mailsim.serve bootstrap`."""
    with sessions(request).begin() as session:
        row = repos.create_project(session, principal.tenant_id, body.name)
        issued = project_keys(session, principal.tenant_id, row.id)
        out = _project_out(row)
    creds = [s.IssuedCredential(api_key=k.api_key, key_id=k.key_id, role=k.role.value)
             for k in issued]  # fmt: skip
    return s.ProjectCreated(**out.model_dump(), credentials=creds)


@router.get("/projects/{project_id}", response_model=s.ProjectOut)
def get_project(request: Request, project_id: str, principal: Inspector) -> s.ProjectOut:
    """The caller's own project only (any other id answers NOT_FOUND)."""
    if project_id != principal.project_id:
        raise NotFound(f"project {project_id} not found")
    with sessions(request)() as session:
        return _project_out(repos.get_project(session, scope(principal)))


@router.delete("/keys/{key_id}", status_code=204, response_class=Response)
def revoke_api_key(request: Request, key_id: str, principal: Controller) -> Response:
    """Revoke any key of the caller's project (including agent keys and the caller's own).
    Keys of other projects or tenants answer NOT_FOUND."""
    with sessions(request).begin() as session:
        if not revoke_scoped_key(session, principal.tenant_id, principal.project_id, key_id):
            raise NotFound(f"key {key_id} not found")
    return Response(status_code=204)


# ---- scenarios -------------------------------------------------------------------------
def _version_out(row: m.ScenarioVersion, key: str) -> s.ScenarioVersionOut:
    return s.ScenarioVersionOut(id=row.id, scenario_key=key, version=row.version,
                                spec_hash=row.spec_hash, created_at=row.created_at)  # fmt: skip


@router.post("/scenarios", response_model=s.ScenarioVersionOut, status_code=201)
def create_scenario(
    request: Request, body: s.ScenarioCreate, principal: Controller, idempotency_key: Idem = None
) -> Response:
    """Publish a scenario version from YAML/JSON text or a parsed document plus base64
    fixtures. Inline evaluation assertions are split off into an evaluator-only answer key."""

    def publish() -> s.ScenarioVersionOut:
        with authoring.tmpdir() as tmp:
            loaded = load_scenario(authoring.materialize(body, Path(tmp).resolve()))
        with sessions(request).begin() as session:
            row = create_scenario_version(session, scope(principal), loaded, blobs(request))
            return _version_out(row, loaded.key)

    return idempotent(request, principal, idempotency_key, _payload(body), 201, publish)


def _scenario_out(session: Session, row: m.Scenario) -> s.ScenarioOut:
    versions = list(
        session.execute(
            select(m.ScenarioVersion)
            .where(m.ScenarioVersion.scenario_id == row.id)
            .order_by(m.ScenarioVersion.version)
        ).scalars()
    )
    latest = _version_out(versions[-1], row.key) if versions else None
    return s.ScenarioOut(id=row.id, key=row.key, latest_version=latest,
                         versions=[v.version for v in versions])  # fmt: skip


def _scenarios(principal: Principal) -> Select[m.Scenario]:
    return select(m.Scenario).where(
        m.Scenario.tenant_id == principal.tenant_id, m.Scenario.project_id == principal.project_id
    )


@router.get("/scenarios", response_model=list[s.ScenarioOut])
def list_scenarios(
    request: Request, principal: Inspector, tag: str | None = None, q: str | None = None
) -> list[s.ScenarioOut]:
    """Scenarios in the caller's project; `tag` matches the latest version's tags, `q` is a
    case-insensitive substring of the key or the latest title."""
    out: list[s.ScenarioOut] = []
    needle = (q or "").casefold()
    with sessions(request)() as session:
        for row in session.execute(_scenarios(principal).order_by(m.Scenario.key)).scalars():
            spec = session.execute(
                select(m.ScenarioVersion.spec)
                .where(m.ScenarioVersion.scenario_id == row.id)
                .order_by(m.ScenarioVersion.version.desc())
                .limit(1)
            ).scalar_one()  # a Scenario row is only created together with its first version
            meta = spec["scenario"]
            if tag is not None and tag not in meta.get("tags", []):
                continue
            if needle not in f"{row.key}\n{meta.get('title', '')}".casefold():
                continue
            out.append(_scenario_out(session, row))
    return out


@router.get("/scenarios/{scenario_key}", response_model=s.ScenarioOut)
def get_scenario(request: Request, scenario_key: str, principal: Inspector) -> s.ScenarioOut:
    with sessions(request)() as session:
        row = session.execute(
            _scenarios(principal).where(m.Scenario.key == scenario_key)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound(f"scenario {scenario_key} not found")
        return _scenario_out(session, row)


@router.post("/scenarios/{scenario_key}/validate", response_model=s.ValidationResult)
def validate_scenario(
    scenario_key: str, body: s.ScenarioCreate, principal: Controller
) -> s.ValidationResult:
    """Validate schema, references, fixtures and assertions without storing anything."""
    report = authoring.validate_draft(body)
    errors = report.errors
    if report.scenario_id is not None and report.scenario_id != scenario_key:
        errors.append(s.ValidationIssue(
            path="scenario.id",
            message=f"scenario id {report.scenario_id!r} does not match {scenario_key!r}"))  # fmt: skip
    return s.ValidationResult(ok=not errors, errors=errors, warnings=report.warnings)


# ---- answer keys (evaluator-only content) ----------------------------------------------
def _answer_key_out(row: m.AnswerKey, key: str) -> s.AnswerKeyOut:
    """Metadata only: assertion content never leaves through the API."""
    return s.AnswerKeyOut(id=row.id, scenario_key=key, version=row.version,
                          spec_hash=row.spec_hash, scenario_versions=row.scenario_versions,
                          source="inline" if row.source == "inline" else "file",
                          created_at=row.created_at)  # fmt: skip


@router.post(
    "/scenarios/{scenario_key}/answer-keys", response_model=s.AnswerKeyOut, status_code=201
)
def create_answer_key(
    request: Request, scenario_key: str, body: s.AnswerKeyCreate, principal: Inspector
) -> s.AnswerKeyOut:
    """Publish a hidden answer-key version (controller or evaluator). The response is
    metadata only. Runs the checks of `.../answer-keys/validate` (400 with `details.issues`);
    references are checked against the compatible scenario versions that exist now (all of
    them when `scenario_versions` is omitted); later scenario versions do not re-check it."""
    data = authoring.answer_key_data(body.source, body.document)
    try:
        doc = AnswerKeyDoc.model_validate(data)
    except ValidationError as exc:
        found = authoring.answer_key_errors(exc, data)
        raise InvalidRequest("answer key validation failed", details={
            "errors": [{"loc": e.loc, "msg": e.message} for e in found],
            "issues": [{"path": e.path, "message": e.message} for e in found],
        }) from None  # fmt: skip
    if doc.answer_key.scenario != scenario_key:
        raise InvalidRequest(f"answer key is for {doc.answer_key.scenario!r}, not {scenario_key!r}")
    with sessions(request).begin() as session:
        row = keys_service.create_answer_key_version(session, scope(principal), doc,
                                                     blobs(request))  # fmt: skip
        return _answer_key_out(row, scenario_key)


@router.get("/scenarios/{scenario_key}/answer-keys", response_model=list[s.AnswerKeyOut])
def list_answer_keys(
    request: Request, scenario_key: str, principal: Evaluator
) -> list[s.AnswerKeyOut]:
    with sessions(request)() as session:
        rows = keys_service.list_answer_keys(session, scope(principal), scenario_key)
        return [_answer_key_out(r, scenario_key) for r in rows]


# ---- runs ------------------------------------------------------------------------------
def _revoke_agent_keys(request: Request, principal: Principal, run_id: str) -> None:
    with sessions(request).begin() as session:
        revoke_agent_keys(session, principal.tenant_id, principal.project_id, run_id)


def _agent_credential(request: Request, principal: Principal, run_id: str) -> s.AgentCredential:
    """Issue an agent key bound to the run and the scenario's agent mailbox, revoking every
    earlier agent key of the run (one live agent credential per run)."""
    ctl = controller(request, principal)
    with sessions(request)() as session:
        run = repos.get_run(session, scope(principal), run_id)
        if run.status in {str(st) for st in TERMINAL} or run.replay_of is not None:
            raise Conflict(f"run is {run.status}{' (a replay)' if run.replay_of else ''}; agent "
                           "credentials are only issued for live runs",
                           details={"status": run.status,
                                    **({"replay_of": run.replay_of} if run.replay_of else {})})  # fmt: skip
        sv = session.get(m.ScenarioVersion, run.scenario_version_id)
        assert sv is not None
        body = sv.spec["scenario"]
    persona = body["agent"]["mailbox"]
    address = next(p["email"] for p in body["personas"] if p["id"] == persona).lower()
    mailbox_id = ctl.mailbox_id_for(run_id, persona)
    with sessions(request).begin() as session:
        key = issue_key(session, tenant_id=principal.tenant_id, project_id=principal.project_id,
                        role=Role.AGENT, run_id=run_id, mailbox_id=mailbox_id,
                        label=f"agent for {run_id}")  # fmt: skip
        revoke_agent_keys(session, principal.tenant_id, principal.project_id, run_id,
                          keep=key.key_id)  # fmt: skip
    return s.AgentCredential(api_key=key.api_key, mailbox_id=mailbox_id,
                             mailbox_address=address, run_id=run_id)  # fmt: skip


@router.post("/scenarios/{scenario_key}/runs", response_model=s.RunCreated, status_code=201)
def create_run(
    request: Request,
    scenario_key: str,
    body: s.RunCreate,
    principal: Controller,
    idempotency_key: Idem = None,
) -> Response:
    """Create (and by default start) an isolated run. The agent's key is returned ONCE here;
    an idempotent replay returns it redacted (mint another with
    `POST /v1/runs/{run_id}/agent-credentials`)."""

    def create() -> s.RunCreated:
        ctl = controller(request, principal)
        with sessions(request)() as session:
            sv_id = scenario_version(session, principal, scenario_key, body.scenario_version).id
        run_id = ctl.create_run(sv_id, checks=body.checks, agent_build=body.agent_build)
        if body.start:
            ctl.start_run(run_id)
        cred = _agent_credential(request, principal, run_id)
        return s.RunCreated(run=run_out(request, principal, run_id), agent_credentials=[cred])

    return idempotent(request, principal, idempotency_key, _payload(body), 201, create,
                      redact=("api_key",))  # fmt: skip


@router.post("/runs/{run_id}/agent-credentials", response_model=s.AgentCredential, status_code=201)
def create_agent_credential(
    request: Request, run_id: str, principal: Controller
) -> s.AgentCredential:
    """Issue a new agent key for a run (e.g. after a lost or redacted response). Earlier agent
    keys of the run are revoked."""
    return _agent_credential(request, principal, run_id)


@router.get("/me", response_model=s.MeOut, tags=["meta"])
def me(principal: Authenticated) -> s.MeOut:
    """Identity of the calling credential (any role)."""
    if principal.local_operator:
        return s.MeOut(role="operator", tenant_id=principal.tenant_id,
                       project_id=principal.project_id, run_id=None, mailbox_id=None,
                       key_id=None, local_no_auth=True)  # fmt: skip
    return s.MeOut(role=principal.role.value, tenant_id=principal.tenant_id,
                   project_id=principal.project_id, run_id=principal.run_id,
                   mailbox_id=principal.mailbox_id, key_id=principal.key_id)  # fmt: skip


@router.get("/runs", response_model=s.RunPage)
def list_runs(
    request: Request,
    principal: Inspector,
    scenario_key: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    include_replays: bool = False,
) -> s.RunPage:
    """Runs in the caller's project, newest first (by creation time, then id); filter by
    `scenario_key` and/or `status`; follow `next_cursor` until it is null.

    Replay runs (`replay_of` set, status REPLAYED) are copies of another run's log, not agent
    runs: they are left out unless `include_replays=true` (or `status=REPLAYED`)."""
    stmt = (
        select(m.Run, m.ScenarioVersion.version, m.Scenario.key)
        .join(m.ScenarioVersion, m.ScenarioVersion.id == m.Run.scenario_version_id)
        .join(m.Scenario, m.Scenario.id == m.ScenarioVersion.scenario_id)
        .where(m.Run.tenant_id == principal.tenant_id, m.Run.project_id == principal.project_id)
    )
    if scenario_key is not None:
        stmt = stmt.where(m.Scenario.key == scenario_key)
    if status is not None:
        stmt = stmt.where(m.Run.status == status)
    if not include_replays and status != RunStatus.REPLAYED:
        stmt = stmt.where(m.Run.replay_of.is_(None))
    if cursor is not None:
        stamp, last_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(m.Run.created_at, m.Run.id) < (parse_ts(stamp), last_id))
    with sessions(request)() as session:
        rows = session.execute(
            stmt.order_by(m.Run.created_at.desc(), m.Run.id.desc()).limit(page_size + 1)
        ).all()
    items = [
        s.RunOut(id=r.id, status=r.status, epoch=r.epoch,
                 virtual_time=iso(r.virtual_time) if r.virtual_time else None,
                 agent_actions=r.agent_actions, replay_of=r.replay_of, scenario_key=key,
                 scenario_version=version,
                 agent_build=r.config.get("evaluation", {}).get("agent_build"))
        for r, version, key in rows[:page_size]
    ]  # fmt: skip
    nxt = None
    if len(rows) > page_size:
        last = rows[page_size - 1][0]
        nxt = encode_cursor(iso(last.created_at), last.id)
    return s.RunPage(items=items, next_cursor=nxt)


@router.get("/runs/{run_id}", response_model=s.RunOut)
def get_run(request: Request, run_id: str, principal: Inspector) -> s.RunOut:
    return run_out(request, principal, run_id)


@router.post("/runs/{run_id}/start", response_model=s.RunOut)
def start_run(request: Request, run_id: str, principal: Controller) -> s.RunOut:
    controller(request, principal).start_run(run_id)
    return run_out(request, principal, run_id)


@router.post("/runs/{run_id}/cancel", response_model=s.RunOut)
def cancel_run(
    request: Request, run_id: str, principal: Controller, idempotency_key: Idem = None
) -> Response:
    def cancel() -> s.RunOut:
        controller(request, principal).cancel_run(run_id)
        _revoke_agent_keys(request, principal, run_id)  # a cancelled run has no agent
        return run_out(request, principal, run_id)

    return idempotent(request, principal, idempotency_key, {}, 200, cancel)


@router.post("/runs/{run_id}/reset", response_model=s.RunOut)
def reset_run(
    request: Request, run_id: str, principal: Controller, idempotency_key: Idem = None
) -> Response:
    """Restore the scenario baseline (new epoch). The run is READY again; start it."""

    def reset() -> s.RunOut:
        controller(request, principal).reset_run(run_id)
        return run_out(request, principal, run_id)

    return idempotent(request, principal, idempotency_key, {}, 200, reset)


@router.get("/runs/{run_id}/events", response_model=s.EventPage)
def run_events(
    request: Request,
    run_id: str,
    principal: Inspector,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    event_type: Annotated[str | None, Query(alias="type")] = None,
    actor: str | None = None,
    epoch: Annotated[int | None, Query(ge=0)] = None,
) -> s.EventPage:
    """Event trace in sequence order, optionally filtered by event `type`, `actor` and
    `epoch`; follow `next_cursor` (with the same filters) until it is null."""
    after = decode_sequence_cursor(cursor)
    rows, nxt_seq = inspector(request, principal).events_page(
        run_id,
        after_sequence=after,
        limit=page_size,
        event_type=event_type,
        actor=actor,
        epoch=epoch,
    )
    items = [s.EventOut(**r) for r in rows]
    nxt = encode_sequence_cursor(nxt_seq) if nxt_seq is not None else None
    return s.EventPage(items=items, next_cursor=nxt)


@router.post("/runs/{run_id}/replay", response_model=s.ReplayOut)
def replay_run(
    request: Request, run_id: str, principal: Controller, idempotency_key: Idem = None
) -> Response:
    """Copy the run's recorded event log into a new run and compare state hashes. The copy
    (`replay_of` = this run) ends REPLAYED (or the original's final status if it had ended):
    it takes no commands, cannot be evaluated (409), and is left out of /accuracy and of the
    runs list unless `include_replays=true`."""

    def replay() -> s.ReplayOut:
        r = controller(request, principal).replay_run(run_id)
        return s.ReplayOut(replay_run_id=r.replay_run_id, matches=r.matches,
                           original_hash=r.original_hash, replay_hash=r.replay_hash,
                           event_count=r.event_count, missing_blobs=r.missing_blobs)  # fmt: skip

    return idempotent(request, principal, idempotency_key, {}, 200, replay)


def _artifacts(request: Request, principal: Principal, run_id: str) -> list[s.ArtifactOut]:
    """Artifacts of a run; reports only for evaluators."""
    view = inspector(request, principal)
    state = view.state(run_id)  # scope-checked
    out = [s.ArtifactOut(kind="trace", ref=f"/v1/runs/{run_id}/events",
                         content_type="application/json")]  # fmt: skip

    def url(ref: str) -> str:
        return f"/v1/runs/{run_id}/artifacts/{ref}"

    seen: set[str] = set()
    for row in [*state["messages"], *state["sink_messages"]]:
        ref = row["raw_blob_ref"]
        if ref and ref not in seen:
            seen.add(ref)
            out.append(s.ArtifactOut(kind="raw_mime", ref=ref, content_type="message/rfc822",
                                     url=url(ref)))  # fmt: skip
    for att in state["attachments"]:
        if att["blob_ref"] not in seen:
            seen.add(att["blob_ref"])
            out.append(s.ArtifactOut(kind="attachment", ref=att["blob_ref"],
                                     content_type=att["mime_type"], size=att["size"],
                                     url=url(att["blob_ref"])))  # fmt: skip
    if isinstance(view, EvaluatorView):
        with sessions(request)() as session:
            reports = session.execute(
                select(m.ReportRow)
                .join(m.Evaluation, m.Evaluation.id == m.ReportRow.evaluation_id)
                .where(m.Evaluation.run_id == run_id)
                .order_by(m.ReportRow.created_at, m.ReportRow.id)
            ).scalars()
            out += [s.ArtifactOut(kind="report", ref=r.blob_ref, url=url(r.blob_ref),
                                  content_type=REPORT_TYPES.get(r.format,
                                                                "application/octet-stream"))
                    for r in reports]  # fmt: skip
    return out


@router.get("/runs/{run_id}/artifacts", response_model=list[s.ArtifactOut])
def run_artifacts(request: Request, run_id: str, principal: Inspector) -> list[s.ArtifactOut]:
    """Trace, raw MIME and attachment blobs; evaluation reports for evaluators only. Blob
    artifacts carry a `url` that downloads them (`GET /v1/runs/{run_id}/artifacts/{ref}`)."""
    return _artifacts(request, principal, run_id)


@router.get(
    "/runs/{run_id}/artifacts/{artifact_id}",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def run_artifact(request: Request, run_id: str, artifact_id: str, principal: Inspector) -> Response:
    """Download one listed blob artifact (`artifact_id` = its `ref`) as an attachment. Only
    blobs this run lists for the caller are served (reports: evaluators only); 404 otherwise.
    Served with `Content-Security-Policy: sandbox`, `X-Content-Type-Options: nosniff` and the
    recorded content type only when it is a passive type (else application/octet-stream)."""
    evaluator = isinstance(inspector(request, principal), EvaluatorView)
    found: tuple[str, str] | None = None  # (content type, file name)
    with sessions(request)() as session:
        repos.get_run(session, scope(principal), run_id)  # scope-checked
        for model in (m.Message, m.SinkMessage):
            if session.scalar(select(model.id).where(model.run_id == run_id,
                                                     model.raw_blob_ref == artifact_id).limit(1)):  # fmt: skip
                found = ("message/rfc822", f"{run_id}-{artifact_id[:12]}.eml")
                break
        if found is None:
            att = session.execute(
                select(m.Attachment.mime_type, m.Attachment.filename)
                .where(m.Attachment.run_id == run_id, m.Attachment.blob_ref == artifact_id)
                .limit(1)
            ).first()
            if att is not None:
                found = (att.mime_type, att.filename)
        if found is None and evaluator:
            rep = session.execute(
                select(m.ReportRow.evaluation_id, m.ReportRow.format)
                .join(m.Evaluation, m.Evaluation.id == m.ReportRow.evaluation_id)
                .where(m.Evaluation.run_id == run_id, m.ReportRow.blob_ref == artifact_id)
                .limit(1)
            ).first()
            if rep is not None:
                found = (REPORT_TYPES.get(rep.format, "application/octet-stream"),
                         report_filename(run_id, rep.evaluation_id, rep.format))  # fmt: skip
    if found is None:
        raise NotFound(f"artifact {artifact_id[:80]} not found for run {run_id}")
    return Response(blobs(request).get(artifact_id),
                    headers=safe_download_headers(found[1], found[0]))  # fmt: skip


def report_filename(run_id: str, evaluation_id: str, fmt: str) -> str:
    return f"{run_id}-{evaluation_id}.{REPORT_EXTENSIONS.get(fmt, 'bin')}"


# ---- evaluations (WS-B service) --------------------------------------------------------
@router.get("/scenarios/{scenario_key}/accuracy", response_model=BuildComparison)
def scenario_accuracy(
    request: Request,
    scenario_key: str,
    principal: Evaluator,
    answer_key_version: int | None = None,
    builds: Annotated[list[str] | None, Query()] = None,
    baseline: str | None = None,
) -> BuildComparison:
    return _evaluations(request).accuracy(scope(principal), scenario_key,
                                          answer_key_version=answer_key_version, builds=builds,
                                          baseline=baseline)  # fmt: skip


@router.post("/runs/{run_id}/evaluations", response_model=EvaluationReport, status_code=201)
def evaluate_run(
    request: Request,
    run_id: str,
    body: s.EvaluationRequest,
    principal: Evaluator,
    idempotency_key: Idem = None,
) -> Response:
    """Grade the recorded run with an answer key (default: the latest compatible version).

    404 when the run or the requested answer-key version does not exist; 409 CONFLICT when
    the key is not compatible with the run's scenario version (`details.answer_key_version`,
    `details.scenario_versions`, `details.run_scenario_version`) and for replay runs
    (`details.replay_of`: evaluate the original run instead)."""
    service = _evaluations(request)

    def evaluate() -> EvaluationReport:
        report = service.evaluate(scope(principal), run_id,
                                  answer_key_version=body.answer_key_version, checks=body.checks,
                                  complete_run=body.complete_run)  # fmt: skip
        with sessions(request)() as session:
            status = repos.get_run(session, scope(principal), run_id).status
        if status in TERMINAL:  # completed (PASSED/FAILED) through this call: agent is done
            _revoke_agent_keys(request, principal, run_id)
        return report

    return idempotent(request, principal, idempotency_key, _payload(body), 201, evaluate)


@router.get("/runs/{run_id}/evaluations", response_model=list[EvaluationReport])
def list_evaluations(request: Request, run_id: str, principal: Evaluator) -> list[EvaluationReport]:
    return _evaluations(request).list(scope(principal), run_id)


@router.get("/runs/{run_id}/results", response_model=EvaluationReport)
def run_results(request: Request, run_id: str, principal: Evaluator) -> EvaluationReport:
    """Latest evaluation of the run (spec 7.1 /results)."""
    return _evaluations(request).latest(scope(principal), run_id)


@router.get(
    "/runs/{run_id}/evaluations/{evaluation_id}/report",
    response_class=Response,
    responses={200: {"content": {t: {} for t in REPORT_TYPES.values()}}},
)
def evaluation_report(
    request: Request,
    run_id: str,
    evaluation_id: str,
    principal: Evaluator,
    fmt: Annotated[Literal["json", "junit", "markdown"], Query(alias="format")] = "json",
) -> Response:
    """Download one evaluation's report (JSON, JUnit XML or Markdown) as an attachment named
    `<run_id>-<evaluation_id>.<json|xml|md>`."""
    service = _evaluations(request)
    sc = scope(principal)
    if evaluation_id not in {r.evaluation_id for r in service.list(sc, run_id)}:
        raise NotFound(f"evaluation {evaluation_id} not found")
    name = report_filename(run_id, evaluation_id, fmt)
    return Response(service.report_blob(sc, evaluation_id, fmt),
                    headers=safe_download_headers(name, REPORT_TYPES[fmt]))  # fmt: skip
