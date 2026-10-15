"""EvaluationService: the frozen entry point WS-A's REST layer calls (interface v1).

evaluate -> grade the RECORDED run with one answer-key version (pure engine), persist the
report + per-assertion results + JSON/JUnit/Markdown blobs, and optionally complete the run.

Run status rules: only the evaluation that completes a RUNNING run sets PASSED/FAILED, pinned
to the last evaluated event sequence. INCONCLUSIVE never completes a run. Re-evaluating a
terminal run stores a new evaluation and leaves the status alone. If the agent acted after
the evaluation was taken (CONFLICT on completion) the run is re-evaluated and completion is
retried; if it keeps moving, the last evaluation is stored without completing the run and the
report says so under `reproducibility.completion`.
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
        completion: dict[str, Any] = {"requested": complete_run, "completed": False,
                                      "attempts": 0, "reason": ""}  # fmt: skip
        report: EvaluationReport | None = None
        for attempt in range(1, self._attempts + 1):
            completion["attempts"] = attempt
            report = evaluate_run(view, run_id, answer_key_version=answer_key_version,
                                  checks=checks, judge=self._judge,
                                  evaluation_id=new_control_id("evl"), now=self._clock())  # fmt: skip
            status = report.reproducibility.get("run_status")
            if not complete_run:
                completion["reason"] = "completion not requested"
            elif status != "RUNNING":
                completion["reason"] = f"run is {status}; status left unchanged"
            elif report.verdict is Verdict.INCONCLUSIVE:
                completion["reason"] = "an INCONCLUSIVE evaluation never completes a run"
            else:
                try:
                    view.complete_run(run_id, report.verdict.value,
                                      evaluation_id=report.evaluation_id,
                                      evaluated_through_sequence=int(
                                          report.reproducibility["evaluated_through_sequence"]))  # fmt: skip
                except Conflict:
                    completion["reason"] = "the run changed after it was evaluated"
                    continue  # grade the new events too, then try again
                except RunNotActive:
                    completion["reason"] = "the run stopped RUNNING before completion"
                else:
                    completion["completed"] = True
                    completion["status"] = report.verdict.value
                    completion["reason"] = ""
            break
        else:
            completion["reason"] = (f"the run kept changing; not completed after "
                                    f"{self._attempts} attempt(s)")  # fmt: skip
        assert report is not None
        report = report.model_copy(
            update={"reproducibility": {**report.reproducibility, "completion": completion}}
        )
        self._persist(report)
        return report

    def _persist(self, report: EvaluationReport) -> None:
        blobs = {fmt: self._blobs.put(render(report, fmt)) for fmt in FORMATS}
        with self._sf.begin() as s:
            store.save_evaluation(s, report, report.reproducibility.get("answer_key_id"))
            for fmt, ref in blobs.items():
                store.add_report_blob(s, report.evaluation_id, fmt, ref)

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
        version with stored evaluations). Each run counts once: its latest evaluation."""
        with self._sf() as s:
            trials = store.trials_for_scenario(s, scope, scenario_key)
        if not trials:
            raise NotFound(f"no stored evaluations for scenario {scenario_key}")
        version = answer_key_version or max(t.answer_key_version for t in trials)
        per_run = {t.run_id: t for t in trials if t.answer_key_version == version}
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
