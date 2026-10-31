"""Run a regression suite against one agent build: N trials per scenario, then grade.

One trial = create a run (tagged with the build id) -> start it -> for each round: start the
agent through the runner, then advance virtual time (persona replies and scheduled events
fire at their due times) -> evaluate the RECORDED run with the pack's answer-key version
(completing the run) -> revoke the trial's agent keys. An agent error (non-zero exit,
timeout, exception) ends the run as ERROR and the trial counts as not passed; it is still
graded, so the report shows what the agent did before it failed.

Each trial records what spec 9.4 asks for: scenario id + immutable version, agent build,
simulator/profile versions, LLM provider/model, seed, final state hash and event count (the
event sequence, final mailbox state and per-assertion evidence stay stored under the run and
evaluation ids), assertion results, wall and virtual duration, action counts and model usage.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from mailsim import __version__
from mailsim.api.auth import issue_key, revoke_agent_keys
from mailsim.auth.principal import Role
from mailsim.bench.report import summarize
from mailsim.bench.suite import PublishedScenario
from mailsim.domain.states import TERMINAL
from mailsim.engine.facades import AgentMailbox, Controller
from mailsim.evaluation.contract import EvaluationReport, Status, Verdict
from mailsim.store.repos import Scope

SCHEMA = "mailsim.bench/1"

# Agent-facing interfaces and the compatibility profile each implements (docs/compat). The
# profiles are versioned with the simulator: `simulator.version` pins their behaviour.
COMPAT_PROFILES = {
    "rest": "mailsim REST /v1 (openapi/openapi.json)",
    "gmail": "Gmail API v1 (docs/compat/gmail.md)",
    "graph": "Microsoft Graph v1.0 mail (docs/compat/graph.md)",
    "imap": "IMAP4rev1 (docs/compat/imap-smtp.md)",
    "smtp": "SMTP submission (docs/compat/imap-smtp.md)",
    "mcp": "mailsim MCP (docs/compat/mcp.md)",
}


class AgentOutcome(Protocol):
    @property
    def ok(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    def to_dict(self) -> dict[str, Any]: ...


class AgentRunner(Protocol):
    """mailsim.runner.command.CommandRunner / mailsim.runner.python.CallableRunner."""

    @property
    def needs_api_key(self) -> bool: ...

    def describe(self) -> dict[str, Any]: ...

    def run(self, *, mailbox: Any, env: Mapping[str, str], timeout: float) -> AgentOutcome: ...


class Evaluator(Protocol):
    def evaluate(self, scope: Scope, run_id: str, *, answer_key_version: int | None,
                 checks: str | None, complete_run: bool) -> EvaluationReport: ...  # fmt: skip


@dataclass(frozen=True)
class BenchConfig:
    build: str
    trials: int = 1
    rounds: int = 1
    advance_seconds: float = 0.0
    timeout_seconds: float = 600.0
    checks: str | None = None
    agent_model: str | None = None
    min_pass_rate: float = 1.0
    max_variance: float | None = None
    api_url: str | None = None


@dataclass
class BenchDeps:
    engine: Any  # SimEngine: only handed to the facades (interfaces never import engine.core)
    session_factory: Any
    scope: Scope
    evaluator: Evaluator
    advance: Callable[[Controller, str, float], dict[str, Any]]  # personas.runtime.advance_clock
    llm: dict[str, Any]  # provider/model of the persona + judge gateway
    llm_usage: Callable[[str], dict[str, Any]] | None = None
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def run_bench(deps: BenchDeps, runner: AgentRunner, scenarios: Sequence[PublishedScenario],
              config: BenchConfig) -> dict[str, Any]:  # fmt: skip
    started_at, t0 = deps.now(), deps.clock()
    ctl = Controller(deps.engine, deps.scope)
    results = []
    for scenario in scenarios:
        trials = [_trial(deps, ctl, runner, scenario, config, n)
                  for n in range(1, config.trials + 1)]  # fmt: skip
        results.append({**scenario.to_dict(), "trials": trials})
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "build": {
            "id": config.build,
            "agent_model": config.agent_model,
            "runner": runner.describe(),
        },  # fmt: skip
        "simulator": {"version": __version__, "profiles": dict(COMPAT_PROFILES)},
        "llm": dict(deps.llm),
        "project_id": deps.scope.project_id,
        "config": {k: v for k, v in asdict(config).items() if k not in ("build", "agent_model")},
        "started_at": started_at.isoformat(),
        "duration_seconds": round(deps.clock() - t0, 6),
        "scenarios": results,
    }
    return summarize(doc)


def _agent_env(ctl: Controller, run_id: str, scenario: PublishedScenario,
               config: BenchConfig) -> dict[str, str]:  # fmt: skip
    agent = next(mb for mb in ctl.list_mailboxes(run_id) if mb["is_agent"])
    env = {"MAILSIM_RUN_ID": run_id, "MAILSIM_MAILBOX_ID": agent["id"],
           "MAILSIM_MAILBOX_ADDRESS": agent["email"], "MAILSIM_AGENT_BUILD": config.build}  # fmt: skip
    if config.api_url:
        env["MAILSIM_API_URL"] = config.api_url
    return env


def _trial(deps: BenchDeps, ctl: Controller, runner: AgentRunner, scenario: PublishedScenario,
           config: BenchConfig, n: int) -> dict[str, Any]:  # fmt: skip
    t0 = deps.clock()
    run_id = ctl.create_run(scenario.scenario_version_id, checks=config.checks,
                            agent_build=config.build)  # fmt: skip
    ctl.start_run(run_id)
    env = _agent_env(ctl, run_id, scenario, config)
    mailbox = AgentMailbox(deps.engine, deps.scope, run_id, env["MAILSIM_MAILBOX_ID"])
    if runner.needs_api_key:
        with deps.session_factory.begin() as s:
            env["MAILSIM_API_KEY"] = issue_key(
                s, tenant_id=deps.scope.tenant_id, project_id=deps.scope.project_id,
                role=Role.AGENT, run_id=run_id, mailbox_id=env["MAILSIM_MAILBOX_ID"],
                label=f"bench agent for {run_id}",
            ).api_key  # fmt: skip
    rounds: list[dict[str, Any]] = []
    error: str | None = None
    try:
        for r in range(1, config.rounds + 1):
            env.update(MAILSIM_BENCH_TRIAL=str(n), MAILSIM_BENCH_ROUND=str(r))
            result = runner.run(mailbox=mailbox, env=dict(env), timeout=config.timeout_seconds)
            entry = result.to_dict()
            rounds.append(entry)
            if not result.ok:
                error = f"round {r}: {result.error}"
                break
            if config.advance_seconds > 0 and ctl.run_info(run_id)["status"] == "RUNNING":
                entry["clock"] = deps.advance(ctl, run_id, config.advance_seconds)
        if error is not None and ctl.run_info(run_id)["status"] == "RUNNING":
            ctl.fail_run_with_error(run_id, f"agent error: {error}"[:500])
        report = deps.evaluator.evaluate(deps.scope, run_id,
                                         answer_key_version=scenario.answer_key_version,
                                         checks=config.checks, complete_run=True)  # fmt: skip
    finally:
        if ctl.run_info(run_id)["status"] not in {str(s) for s in TERMINAL}:
            ctl.cancel_run(run_id)  # e.g. INCONCLUSIVE: never leave a live run behind
        with deps.session_factory.begin() as s:
            revoke_agent_keys(s, deps.scope.tenant_id, deps.scope.project_id, run_id)
    return _trial_record(deps, ctl, run_id, n, report, rounds, error, deps.clock() - t0)


def _trial_record(deps: BenchDeps, ctl: Controller, run_id: str, n: int,
                  report: EvaluationReport, rounds: list[dict[str, Any]], error: str | None,
                  duration: float) -> dict[str, Any]:  # fmt: skip
    eff, rep = report.efficiency, report.reproducibility
    interfaces = sorted({a["interface"] for a in ctl.actions(run_id)
                         if a["actor"] == "agent" and a.get("interface")})  # fmt: skip
    agent_usage = [r["usage"] for r in rounds if r.get("usage")]
    outcome = "ERROR" if error is not None else report.verdict.value
    return {
        "trial": n,
        "run_id": run_id,
        "evaluation_id": report.evaluation_id,
        "outcome": outcome,
        "passed": error is None and report.verdict is Verdict.PASSED,
        "verdict": report.verdict.value,
        "verdict_reasons": list(report.verdict_reasons),
        "run_status": ctl.run_info(run_id)["status"],
        "error": error,
        "seed": rep.get("seed"),
        "state_hash": rep.get("state_hash"),
        "event_count": rep.get("event_count"),
        "checks_enabled": report.checks_enabled,
        "accuracy": report.accuracy.overall,
        "mandatory_accuracy": report.accuracy.mandatory,
        "assertions": {
            o.assertion_id: o.status.value for o in report.outcomes if o.subject == "agent"
        },  # fmt: skip
        "failed_assertions": [
            o.assertion_id
            for o in report.outcomes
            if o.subject == "agent" and o.status is Status.FAIL
        ],  # fmt: skip
        "duration_seconds": round(duration, 6),
        "virtual_duration_seconds": eff.virtual_duration_seconds,
        "agent_actions": eff.agent_actions,
        "denied_actions": eff.denied_actions,
        "replies_sent": eff.replies_sent,
        "interfaces": interfaces,
        "model_usage": {
            "models": list(rep.get("models") or []),
            "input_tokens": eff.model_input_tokens,
            "output_tokens": eff.model_output_tokens,
            **({"gateway": deps.llm_usage(run_id)} if deps.llm_usage else {}),
            "agent": agent_usage,
        },
        "rounds": rounds,
    }


__all__ = ["COMPAT_PROFILES", "SCHEMA", "AgentRunner", "BenchConfig", "BenchDeps", "run_bench"]
