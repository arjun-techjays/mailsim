"""Shared plumbing for the routers: app-state access and role-facade construction.

Handlers reach the engine only through `facade_for` (the role facade a credential allows);
control-plane records (projects, scenarios, answer keys, keys) go through the scenario and
store services directly, always scoped to the principal's tenant + project.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from mailsim.api import schemas as s
from mailsim.auth.principal import Principal
from mailsim.domain.errors import PermissionDenied, UnsupportedOperation
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
