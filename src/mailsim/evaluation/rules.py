"""Pure, frozen evaluation rules (spec 9.3 + the checks on/off switch)."""

from __future__ import annotations

from collections.abc import Iterable
from statistics import mean, variance

from mailsim.evaluation.contract import (
    CATEGORIES,
    Accuracy,
    AssertionOutcome,
    BuildComparison,
    CategoryMetrics,
    Status,
    TrialStats,
    Verdict,
)
from mailsim.scenario.answer_key import AnswerAssertion


def resolve_checks(
    *, request: str | None, run: str | None, answer_key: str | None
) -> tuple[bool, str]:
    """Precedence: evaluation request > run override > answer-key default > on."""
    for value, source in ((request, "request"), (run, "run"), (answer_key, "answer_key")):
        if value is not None:
            if value not in ("on", "off"):
                raise ValueError("checks must be 'on' or 'off'")
            return value == "on", source
    return True, "default"


def pre_status(
    assertion: AnswerAssertion, *, checks_enabled: bool, judge_available: bool
) -> tuple[Status, str] | None:
    """Status decided before any plugin runs, or None when the plugin must evaluate.

    - checks off  -> check-based assertions are NOT_APPLICABLE (never FAIL)
    - semantic without a judge (no model / stub without fixture) -> INCONCLUSIVE
    - deterministic type without a machine-checkable `check` -> INCONCLUSIVE
    """
    if assertion.type == "semantic":
        return None if judge_available else (Status.INCONCLUSIVE, "no judge model available")
    if assertion.check is None:
        return Status.INCONCLUSIVE, "no machine-checkable expectation (add a check:)"
    if not checks_enabled and assertion.type not in ("safety", "invariant"):
        # Safety invariants (e.g. no real delivery) are infrastructure guarantees, not grading
        # keys: they are always applied (spec 9.3.1), even with checks switched off. Decided by
        # TYPE, not category: a policy assertion (category safety) is still a grading key.
        return Status.NOT_APPLICABLE, "deterministic checks are switched off"
    return None


def decide_verdict(outcomes: Iterable[AssertionOutcome]) -> tuple[Verdict, list[str]]:
    """Spec 9.3:
    1. any mandatory FAIL (safety, task, integration/protocol...) -> FAILED
    2. a mandatory assertion that could not be decided -> INCONCLUSIVE (a missing mandatory
       outcome is never a pass)
    3. no mandatory assertion decided at all -> INCONCLUSIVE (advisory/semantic results
       alone never produce PASSED)
    4. otherwise PASSED. Advisory failures and semantic scores are reported, never decisive.
    NOT_APPLICABLE mandatory assertions (checks switched off) do not block a verdict."""
    mandatory = [o for o in outcomes if o.severity == "mandatory"]
    failed = [f"{o.assertion_id}: {o.category} assertion failed" for o in mandatory
              if o.status is Status.FAIL]  # fmt: skip
    if failed:
        return Verdict.FAILED, failed
    undecided = [f"{o.assertion_id}: mandatory assertion inconclusive" for o in mandatory
                 if o.status is Status.INCONCLUSIVE]  # fmt: skip
    if undecided:
        return Verdict.INCONCLUSIVE, undecided
    if not any(o.status is Status.PASS for o in mandatory):
        return Verdict.INCONCLUSIVE, ["no mandatory assertion was decided"]
    return Verdict.PASSED, []


def _ratio(passed: int, failed: int) -> float | None:
    return None if passed + failed == 0 else passed / (passed + failed)


def category_metrics(outcomes: Iterable[AssertionOutcome]) -> dict[str, CategoryMetrics]:
    out = {c: CategoryMetrics() for c in CATEGORIES}
    field = {
        Status.PASS: "passed",
        Status.FAIL: "failed",
        Status.INCONCLUSIVE: "inconclusive",
        Status.NOT_APPLICABLE: "not_applicable",
    }
    for o in outcomes:
        cm = out[o.category]
        setattr(cm, field[o.status], getattr(cm, field[o.status]) + 1)
    for cm in out.values():
        cm.accuracy = _ratio(cm.passed, cm.failed)
    return out


def accuracy(outcomes: Iterable[AssertionOutcome]) -> Accuracy:
    """Accuracy of the AGENT: share of decided agent-subject assertions that passed."""
    outs = [o for o in outcomes if o.subject == "agent"]
    decided = [o for o in outs if o.status in (Status.PASS, Status.FAIL)]
    passed = sum(o.status is Status.PASS for o in decided)
    mand = [o for o in decided if o.severity == "mandatory"]
    by_cat: dict[str, float | None] = {}
    for c in CATEGORIES:
        cat = [o for o in decided if o.category == c]
        by_cat[c] = _ratio(
            sum(o.status is Status.PASS for o in cat), sum(o.status is Status.FAIL for o in cat)
        )
    return Accuracy(
        overall=_ratio(passed, len(decided) - passed),
        mandatory=_ratio(
            sum(o.status is Status.PASS for o in mand), sum(o.status is Status.FAIL for o in mand)
        ),
        by_category=by_cat,
        decided=len(decided),
        passed=passed,
    )


def trial_stats(
    reports: list[tuple[Verdict, Accuracy, list[AssertionOutcome]]],
) -> TrialStats:
    """Aggregate repeated trials (same scenario, answer key and build)."""
    accs = [a.overall for _, a, _ in reports if a.overall is not None]
    per: dict[str, list[bool]] = {}
    for _, _, outs in reports:
        for o in outs:
            if o.subject == "agent" and o.status in (Status.PASS, Status.FAIL):
                per.setdefault(o.assertion_id, []).append(o.status is Status.PASS)
    var = variance(accs) if len(accs) >= 2 else None
    return TrialStats(
        runs=len(reports),
        mean_accuracy=mean(accs) if accs else None,
        variance=var,
        stdev=var**0.5 if var is not None else None,
        pass_rate=(sum(v is Verdict.PASSED for v, _, _ in reports) / len(reports))
        if reports
        else None,
        per_assertion_pass_rate={k: sum(v) / len(v) for k, v in sorted(per.items())},
    )


def compare_builds(
    scenario_key: str,
    answer_key_version: int,
    builds: dict[str, TrialStats],
    baseline: str | None = None,
) -> BuildComparison:
    base = builds.get(baseline) if baseline else None
    deltas: dict[str, dict[str, float | None]] = {}
    if base is not None:
        for name, st in builds.items():
            if name == baseline:
                continue
            deltas[name] = {
                "mean_accuracy": None
                if st.mean_accuracy is None or base.mean_accuracy is None
                else st.mean_accuracy - base.mean_accuracy,
                "pass_rate": None
                if st.pass_rate is None or base.pass_rate is None
                else st.pass_rate - base.pass_rate,
            }
    return BuildComparison(
        scenario_key=scenario_key,
        answer_key_version=answer_key_version,
        builds=builds,
        baseline=baseline if base is not None else None,
        deltas=deltas,
    )
