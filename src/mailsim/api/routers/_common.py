"""Shared plumbing for the routers: app-state access and role-facade construction.

Handlers reach the engine only through `facade_for` (the role facade a credential allows);
control-plane records (projects, scenarios, answer keys, keys) go through the scenario and
store services directly, always scoped to the principal's tenant + project.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailsim.api import schemas as s
from mailsim.auth.principal import Principal
from mailsim.domain.errors import NotFound, PermissionDenied, UnsupportedOperation
from mailsim.domain.models import AttachmentRef
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView, facade_for
from mailsim.store import models as m
from mailsim.store import repos
from mailsim.store.blob import BlobStore


def app_state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise UnsupportedOperation(f"server is not configured with {name}",
                                   details={"missing": name})  # fmt: skip
    return value


def sessions(request: Request) -> sessionmaker[Session]:
    sf: sessionmaker[Session] = app_state(request, "session_factory")
    return sf


def blobs(request: Request) -> BlobStore:
    store: BlobStore = app_state(request, "blobs")
    return store


def scope(principal: Principal) -> repos.Scope:
    return repos.Scope(principal.tenant_id, principal.project_id)


def _facade[F](request: Request, principal: Principal, kind: type[F]) -> F:
    if principal.local_operator:  # local no-auth operator: controller + evaluator views only
        if kind is AgentMailbox:
            raise PermissionDenied("agent interfaces require an agent API key")
        sc = repos.Scope(principal.tenant_id, principal.project_id)
        engine = app_state(request, "engine")
        view: Controller | EvaluatorView = (
            Controller(engine, sc) if kind is Controller else EvaluatorView(engine, sc)
        )
        return view  # type: ignore[return-value]
    facade = facade_for(app_state(request, "engine"), principal)
    if not isinstance(facade, kind):
        raise PermissionDenied("this credential's role may not call this operation")
    return facade


def controller(request: Request, principal: Principal) -> Controller:
    return _facade(request, principal, Controller)


def evaluator(request: Request, principal: Principal) -> EvaluatorView:
    return _facade(request, principal, EvaluatorView)


def agent(request: Request, principal: Principal) -> AgentMailbox:
    return _facade(request, principal, AgentMailbox)


def inspector(request: Request, principal: Principal) -> Controller | EvaluatorView:
    """Controller or evaluator view, whichever the credential's role allows."""
    return _facade(request, principal, Controller | EvaluatorView)  # type: ignore[arg-type]


def run_personas(request: Request, ctl: Controller, run_id: str) -> int:
    """Run one persona-runtime step after the clock moved (single scheduler loop per run).

    The runtime is optional (`app.state.persona_runtime_factory`); without it personas stay
    silent. Replies are recorded via `Controller.persona_reply`, so replay never calls a model.
    """
    factory = getattr(request.app.state, "persona_runtime_factory", None)
    if factory is None:
        return 0
    return len(factory(ctl).step(run_id))


def run_out(request: Request, principal: Principal, run_id: str) -> s.RunOut:
    info = inspector(request, principal).run_info(run_id)  # scope-checked (404 otherwise)
    with sessions(request)() as session:
        run = repos.get_run(session, scope(principal), run_id)
        sv = session.get(m.ScenarioVersion, run.scenario_version_id)
        assert sv is not None  # every run (and replay) is created from a scenario version
        scenario = session.get(m.Scenario, sv.scenario_id)
        assert scenario is not None
        build = run.config.get("evaluation", {}).get("agent_build")
    return s.RunOut(
        id=info["id"], status=info["status"], epoch=info["epoch"],
        virtual_time=info["virtual_time"], agent_actions=info["agent_actions"],
        replay_of=info["replay_of"], scenario_key=scenario.key, scenario_version=sv.version,
        agent_build=build,
    )  # fmt: skip


def scenario_version(
    session: Session, principal: Principal, key: str, version: int | None
) -> m.ScenarioVersion:
    """A scenario version of the caller's project (latest when `version` is None) or 404."""
    stmt = (
        select(m.ScenarioVersion)
        .join(m.Scenario, m.Scenario.id == m.ScenarioVersion.scenario_id)
        .where(
            m.Scenario.tenant_id == principal.tenant_id,
            m.Scenario.project_id == principal.project_id,
            m.Scenario.key == key,
        )  # fmt: skip
    )
    if version is not None:
        stmt = stmt.where(m.ScenarioVersion.version == version)
    row = session.execute(
        stmt.order_by(m.ScenarioVersion.version.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        suffix = f" version {version}" if version is not None else ""
        raise NotFound(f"scenario {key}{suffix} not found")
    return row


# ---- attachment downloads ----------------------------------------------------------------
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
_ASCII_SAFE = re.compile(r"[^A-Za-z0-9 !#$&+,.;=@^_`{}~()\[\]-]")
ATTACHMENT_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The attachment bytes, always as a download (`Content-Disposition: "
        "attachment`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`).",
        "content": {"application/octet-stream": {"schema": {"type": "string",
                                                             "format": "binary"}}},
    }
}  # fmt: skip


def content_disposition(filename: str) -> str:
    """`attachment` with an ASCII fallback `filename` and the exact UTF-8 name as `filename*`
    (RFC 6266 / RFC 5987). Control characters, quotes, backslashes and path separators never
    reach the header."""
    name = "".join(ch for ch in filename if ch.isprintable()).replace("/", "_").replace("\\", "_")
    name = name.strip().strip(".") or "attachment"
    fallback = _ASCII_SAFE.sub("_", name)[:150] or "attachment"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name[:255], safe='')}"


def attachment_response(ref: AttachmentRef, data: bytes) -> Response:
    """Bytes of one attachment, served so a browser never renders or sniffs them."""
    media = ref.mime_type.strip().lower()
    return Response(
        content=data,
        media_type=media if _MEDIA_TYPE.fullmatch(media) else "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(ref.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )
