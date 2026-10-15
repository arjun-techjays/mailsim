"""EvaluationService: the frozen entry point WS-A's REST layer calls (interface v1).

evaluate -> grade the RECORDED run with one answer-key version (pure engine), persist the
report + per-assertion results + JSON/JUnit/Markdown blobs, and optionally complete the run.

Run status rules: only the evaluation that completes a RUNNING run sets PASSED/FAILED, pinned
to the last evaluated event sequence. INCONCLUSIVE never completes a run. Re-evaluating a
terminal run stores a new evaluation and leaves the status alone. If the agent acted after
the evaluation was taken (CONFLICT on completion) the run is re-evaluated and completion is
retried. Every evaluation is persisted BEFORE completion is attempted, then its
`reproducibility.completion` is updated with the outcome; an evaluation whose completion
failed stays stored as a non-completing evaluation.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from mailsim.domain.errors import Conflict, InvalidRequest, NotFound, RunNotActive
from mailsim.domain.ids import new_control_id
from mailsim.engine.facades import EvaluatorView
from mailsim.evaluation import store
from mailsim.evaluation.contract import BuildComparison, EvaluationReport, TrialStats, Verdict
from mailsim.evaluation.engine import evaluate_run
from mailsim.evaluation.judge import Judge
from mailsim.evaluation.reports import FORMATS, render
from mailsim.evaluation.rules import compare_builds, trial_stats
from mailsim.store.blob import BlobStore
from mailsim.store.repos import Scope

UNSPECIFIED_BUILD = "unspecified"


def _with_completion(report: EvaluationReport, completion: dict[str, Any]) -> EvaluationReport:
    return report.model_copy(
        update={"reproducibility": {**report.reproducibility, "completion": dict(completion)}}
    )


def _completing(report: EvaluationReport) -> bool:
    return bool((report.reproducibility.get("completion") or {}).get("completed"))


class _CachingJudge:
    """Per-evaluate() memo of judgements keyed by exactly what the judge is shown."""

    def __init__(self, inner: Judge) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}

    def judge(self, *, requirement: str, rubric: str, agent_texts: list[str]) -> dict[str, Any]:
        key = (requirement, rubric, tuple(agent_texts))
        if key not in self._cache:
            self._cache[key] = self._inner.judge(requirement=requirement, rubric=rubric,
                                                 agent_texts=agent_texts)  # fmt: skip
        return self._cache[key]


class EvaluationService:
    def __init__(
        self,
        engine: Any,  # SimEngine; typed loosely so this package never imports engine.core
        session_factory: sessionmaker[Session],
        blobs: BlobStore,
        *,
        judge: Judge | None = None,
        max_completion_attempts: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._sf = session_factory
        self._blobs = blobs
        self._judge = judge
        self._attempts = max(1, max_completion_attempts)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _view(self, scope: Scope) -> EvaluatorView:
        return EvaluatorView(self._engine, scope)

    # ----------------------------------------------------------------------- evaluate
    def evaluate(
        self,
        scope: Scope,
        run_id: str,
        *,
        answer_key_version: int | None,
        checks: str | None,
        complete_run: bool,
    ) -> EvaluationReport:
        if checks not in (None, "on", "off"):
            raise InvalidRequest("checks must be 'on', 'off' or omitted")
        view = self._view(scope)
        # retries re-grade new events only: an unchanged judgement is never paid for twice
        judge = _CachingJudge(self._judge) if self._judge is not None else None
        report: EvaluationReport | None = None
        for attempt in range(1, self._attempts + 1):
            report = evaluate_run(view, run_id, answer_key_version=answer_key_version,
                                  checks=checks, judge=judge,
                                  evaluation_id=new_control_id("evl"), now=self._clock())  # fmt: skip
            completion: dict[str, Any] = {"requested": complete_run, "completed": False,
                                          "attempt": attempt, "reason": ""}  # fmt: skip
            status = report.reproducibility.get("run_status")
            if not complete_run:
                completion["reason"] = "completion not requested"
            elif status != "RUNNING":
                completion["reason"] = f"run is {status}; status left unchanged"
            elif report.verdict is Verdict.INCONCLUSIVE:
                completion["reason"] = "an INCONCLUSIVE evaluation never completes a run"
            else:
                completion["reason"] = "pending completion"
            report = _with_completion(report, completion)
            # persist BEFORE completing: run.completed must only ever name a stored evaluation
            self._persist(report)
            if completion["reason"] != "pending completion":
                return report
            try:
                view.complete_run(run_id, report.verdict.value,
                                  evaluation_id=report.evaluation_id,
                                  evaluated_through_sequence=int(
                                      report.reproducibility["evaluated_through_sequence"]))  # fmt: skip
            except Conflict:
                completion["reason"] = (
                    "the run changed after it was evaluated; superseded by a re-evaluation"
                    if attempt < self._attempts
                    else f"the run kept changing; not completed after {attempt} attempt(s)"
                )
                report = self._update(_with_completion(report, completion))
                continue  # grade the new events too, then try again
            except RunNotActive:
                completion["reason"] = "the run stopped RUNNING before completion"
            else:
                completion.update(completed=True, status=report.verdict.value, reason="")
            return self._update(_with_completion(report, completion))
        assert report is not None
        return report

    def _persist(self, report: EvaluationReport) -> None:
        blobs = {fmt: self._blobs.put(render(report, fmt)) for fmt in FORMATS}
        with self._sf.begin() as s:
            store.save_evaluation(s, report, report.reproducibility.get("answer_key_id"))
            for fmt, ref in blobs.items():
                store.add_report_blob(s, report.evaluation_id, fmt, ref)

    def _update(self, report: EvaluationReport) -> EvaluationReport:
        """Record the completion outcome on an already stored evaluation (and its exports)."""
        blobs = {fmt: self._blobs.put(render(report, fmt)) for fmt in FORMATS}
        with self._sf.begin() as s:
            store.update_report(s, report, blobs)
        return report

    # ----------------------------------------------------------------------- reads
    def list(self, scope: Scope, run_id: str) -> builtins.list[EvaluationReport]:
        self._view(scope).run_info(run_id)  # NOT_FOUND outside the scope
        with self._sf() as s:
            return store.list_for_run(s, scope, run_id)

    def latest(self, scope: Scope, run_id: str) -> EvaluationReport:
        reports = self.list(scope, run_id)
        if not reports:
            raise NotFound(f"run {run_id} has not been evaluated")
        return reports[-1]

    def report_blob(self, scope: Scope, evaluation_id: str, fmt: str) -> bytes:
        if fmt not in FORMATS:
            raise InvalidRequest(f"unknown report format {fmt!r}; use one of {list(FORMATS)}")
        with self._sf() as s:
            ref = store.report_blob_ref(s, scope, evaluation_id, fmt)
        return self._blobs.get(ref)

    # ----------------------------------------------------------------------- accuracy
    def accuracy(
        self,
        scope: Scope,
        scenario_key: str,
        *,
        answer_key_version: int | None,
        builds: builtins.list[str] | None,
        baseline: str | None,
    ) -> BuildComparison:
        """Repeated-trial accuracy per agent build for one key version (default: the latest
        version with stored checks-on evaluations).

        Only comparable evaluations count: checks-off evaluations are excluded (they grade a
        different question), and each run counts once, by the evaluation that completed it if
        any, else its latest checks-on evaluation for that key version. The frozen
        BuildComparison has no field for the excluded count, so it is not reported."""
        with self._sf() as s:
            trials = [t for t in store.trials_for_scenario(s, scope, scenario_key)
                      if t.report.checks_enabled]  # fmt: skip
        if not trials:
            raise NotFound(f"no stored checks-on evaluations for scenario {scenario_key}")
        version = answer_key_version or max(t.answer_key_version for t in trials)
        per_run: dict[str, store.StoredTrial] = {}
        for t in trials:  # oldest first
            if t.answer_key_version != version:
                continue
            current = per_run.get(t.run_id)
            if current is not None and _completing(current.report):
                continue
            per_run[t.run_id] = t
        grouped: dict[str, builtins.list[EvaluationReport]] = {}
        for t in per_run.values():
            grouped.setdefault(t.agent_build or UNSPECIFIED_BUILD, []).append(t.report)
        names = builds if builds is not None else sorted(grouped)
        stats: dict[str, TrialStats] = {
            name: trial_stats([(r.verdict, r.accuracy, r.outcomes) for r in grouped.get(name, [])])
            for name in names
        }
        return compare_builds(scenario_key, version, stats, baseline)


__all__ = ["EvaluationService", "UNSPECIFIED_BUILD"]
