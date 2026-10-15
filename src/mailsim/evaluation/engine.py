"""Pure post-run evaluation: one run x one answer-key version -> EvaluationReport.

Reads ONLY the evaluator view of the RECORDED run (event log, action log, state snapshots,
scenario spec, answer key). It writes nothing and never touches the agent-facing surface;
persistence and run completion are the service's job. Re-running it later with another key
version or checks setting re-grades the same recording without re-running the agent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import mailsim.evaluation.checks  # noqa: F401  (registers the assertion plugins)
from mailsim.engine.snapshot import state_hash
from mailsim.evaluation import registry
from mailsim.evaluation.checks._common import AGENT, action_position, index, outcome
from mailsim.evaluation.contract import AssertionOutcome, Efficiency, EvaluationReport, Status
from mailsim.evaluation.registry import EvaluationContext
from mailsim.evaluation.rules import accuracy, category_metrics, decide_verdict, resolve_checks
from mailsim.scenario.answer_key import AnswerAssertion, AnswerKeyDoc

MAX_READ_ATTEMPTS = 5


class RecordedRun(Protocol):
    """The subset of `engine.facades.EvaluatorView` the evaluation reads."""

    def run_info(self, run_id: str) -> dict[str, Any]: ...
    def state(self, run_id: str) -> dict[str, Any]: ...
    def events(self, run_id: str) -> list[dict[str, Any]]: ...
    def actions(self, run_id: str) -> list[dict[str, Any]]: ...
    def scenario_spec(self, run_id: str) -> dict[str, Any]: ...
    def answer_key(self, run_id: str, version: int | None = None) -> dict[str, Any]: ...
    def state_at(self, run_id: str, up_to_sequence: int) -> dict[str, Any]: ...


def _last_seq(events: list[dict[str, Any]]) -> int:
    return int(events[-1]["sequence"]) if events else 0


def _pin(actions: list[dict[str, Any]], through: int) -> list[dict[str, Any]]:
    """Actions placed within the evaluated log prefix (later ones were never graded)."""
    return [a for a in actions if action_position(a) <= through]


def read_consistent(
    view: RecordedRun, run_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Events, state and actions describing the same log prefix.

    The state is only accepted when no event was appended while it was read; if the run
    keeps moving, the state is rebuilt from the log at the pinned sequence instead. Actions
    are always cut at the pinned sequence by their recorded event-log window.
    """
    events: list[dict[str, Any]] = []
    for _ in range(MAX_READ_ATTEMPTS):
        events = view.events(run_id)
        state = view.state(run_id)
        actions = view.actions(run_id)
        if _last_seq(view.events(run_id)) == _last_seq(events):
            return events, state, _pin(actions, _last_seq(events))
    through = _last_seq(events)
    return events, view.state_at(run_id, through), _pin(view.actions(run_id), through)


def _run_config(events: list[dict[str, Any]]) -> tuple[dict[str, Any], int | None]:
    for e in events:
        if e["type"] == "run.created":
            return dict(e["payload"].get("config") or {}), e["payload"].get("seed")
    return {}, None


def _evaluate_one(ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
    plugin = registry.get(assertion.type)
    if plugin is None:
        return outcome(assertion, Status.INCONCLUSIVE,
                       f"no evaluator registered for type {assertion.type!r}")  # fmt: skip
    try:
        return plugin.evaluate(ctx, assertion)
    except Exception as exc:  # noqa: BLE001 - a broken check is undecided, never a pass
        return outcome(assertion, Status.INCONCLUSIVE,
                       f"evaluator error: {type(exc).__name__}: {str(exc)[:200]}")  # fmt: skip


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _efficiency(ctx: EvaluationContext) -> tuple[Efficiency, list[str]]:
    idx = index(ctx)
    window = idx.events_in(idx.last_seq)
    agent = [o for o in idx.occurrences_in(idx.last_seq) if o.actor == AGENT]
    replies = sum(
        1 for e in window
        if e["type"] == "message.sent" and e["actor"] == AGENT
        and e["payload"]["message"].get("in_reply_to")
    )  # fmt: skip
    started = [e["sequence"] for e in window
               if e["type"] == "run.status_changed" and e["payload"].get("to") == "RUNNING"]  # fmt: skip
    duration = 0.0
    if started:
        t0, t1 = idx.time_at(started[-1]), idx.time_at(idx.last_seq)
        if t0 is not None and t1 is not None:
            duration = max(0.0, (t1 - t0).total_seconds())
    tokens_in = tokens_out = 0
    models: set[str] = set()
    usage: list[dict[str, Any]] = [
        e["payload"].get("generation") or {} for e in window if e["type"] == "persona.generated"
    ]
    usage += [c for c in ctx.extras.get("judge_calls", []) if isinstance(c, dict)]
    for u in usage:
        tokens_in += _int(u.get("input_tokens"))
        tokens_out += _int(u.get("output_tokens"))
        if u.get("model"):
            models.add(":".join(str(x) for x in (u.get("provider"), u["model"]) if x))
    eff = Efficiency(
        agent_actions=len(agent),
        denied_actions=sum(o.outcome != "ok" for o in agent),
        replies_sent=replies,
        virtual_duration_seconds=duration,
        model_input_tokens=tokens_in,
        model_output_tokens=tokens_out,
    )
    return eff, sorted(models)


def evaluate_run(
    view: RecordedRun,
    run_id: str,
    *,
    answer_key_version: int | None = None,
    checks: str | None = None,
    judge: Any | None = None,
    evaluation_id: str,
    now: datetime | None = None,
) -> EvaluationReport:
    info = view.run_info(run_id)
    scenario = view.scenario_spec(run_id)
    key_row = view.answer_key(run_id, answer_key_version)
    body = AnswerKeyDoc.model_validate(key_row["spec"]).answer_key
    events, state, actions = read_consistent(view, run_id)
    config, seed = _run_config(events)
    eval_cfg = config.get("evaluation") or {}
    checks_enabled, checks_source = resolve_checks(
        request=checks, run=eval_cfg.get("checks"), answer_key=body.checks_default
    )
    personas = {mb["owner_id"]: mb["id"] for mb in state.get("mailboxes", [])}
    agent_owner = (config.get("agent") or {}).get("mailbox_owner")
    ctx = EvaluationContext(
        run={**info, "config": config},
        scenario=scenario.get("spec") or {},
        final_state=state,
        events=events,
        actions=actions,
        agent_mailbox_id=personas.get(agent_owner, "") if agent_owner else "",
        persona_mailboxes=personas,
        state_at=lambda seq: view.state_at(run_id, seq),
        judge=judge,
        extras={"checks_enabled": checks_enabled},
    )
    outcomes = [_evaluate_one(ctx, a) for a in body.assertions]
    verdict, reasons = decide_verdict(outcomes)
    efficiency, models = _efficiency(ctx)
    idx = index(ctx)
    spec = scenario.get("spec") or {}
    scen = spec.get("scenario") or {}
    return EvaluationReport(
        evaluation_id=evaluation_id,
        run_id=run_id,
        scenario_key=str(scen.get("id", body.scenario)),
        scenario_version=int(scenario.get("version") or scen.get("version") or 0),
        answer_key_version=body.version,
        answer_key_hash=key_row.get("spec_hash"),
        agent_build=eval_cfg.get("agent_build"),
        checks_enabled=checks_enabled,
        checks_source=checks_source,
        verdict=verdict,
        verdict_reasons=reasons,
        outcomes=outcomes,
        metrics=category_metrics(outcomes),
        accuracy=accuracy(outcomes),
        efficiency=efficiency,
        reproducibility={
            "seed": seed,
            "epoch": (state.get("run") or {}).get("epoch", info.get("epoch")),
            "event_count": len(idx.events_in(idx.last_seq)),
            "evaluated_through_sequence": idx.last_seq,
            "state_hash": state_hash(state),
            "run_status": (state.get("run") or {}).get("status", info.get("status")),
            "scenario_version_id": scenario.get("scenario_version_id"),
            "answer_key_id": key_row.get("id"),
            "answer_key_source": key_row.get("source"),
            "replay_of": info.get("replay_of"),
            "models": models,
        },
        created_at=now or datetime.now(UTC),
    )


__all__ = ["RecordedRun", "evaluate_run", "read_consistent"]
