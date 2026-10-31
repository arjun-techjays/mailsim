"""Compare two bench result documents (a baseline build and a candidate build).

Scenarios are matched by key. Deltas of mean accuracy and pass rate come from the evaluator's
frozen `compare_builds`; per-assertion pass-rate deltas, mean action count and duration are
added. A scenario REGRESSED when its pass rate or mean accuracy dropped by more than the
tolerance, or an assertion's pass rate dropped by more than the tolerance; IMPROVED when one
rose and none dropped. Scenarios whose scenario version, fixture hash or answer-key version
differ are still compared but flagged `comparable: false` (different fixtures or grading).
No database is needed: the inputs are `sim bench run --json` files.
"""

from __future__ import annotations

from typing import Any

from mailsim.bench.run import SCHEMA
from mailsim.domain.errors import InvalidRequest
from mailsim.evaluation.contract import TrialStats
from mailsim.evaluation.rules import compare_builds

PINNED = ("scenario_version", "spec_hash", "answer_key_version")
EPS = 1e-9


def _check(doc: Any, name: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise InvalidRequest(f"{name} is not a `sim bench run` result ({SCHEMA})")
    return doc


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def _stats(sc: dict[str, Any]) -> TrialStats:
    return TrialStats.model_validate({k: sc["stats"].get(k) for k in TrialStats.model_fields})


def _scenario(base: dict[str, Any], cand: dict[str, Any], names: tuple[str, str],
              tolerance: float) -> dict[str, Any]:  # fmt: skip
    b_name, c_name = names
    cmp = compare_builds(base["scenario_key"], int(cand.get("answer_key_version") or 0),
                         {b_name: _stats(base), c_name: _stats(cand)}, baseline=b_name)  # fmt: skip
    deltas: dict[str, float | None] = dict(cmp.deltas[c_name])
    for metric in ("mean_agent_actions", "mean_duration_seconds"):
        deltas[metric] = _delta(base["stats"].get(metric), cand["stats"].get(metric))
    b_rates = base["stats"].get("per_assertion_pass_rate") or {}
    c_rates = cand["stats"].get("per_assertion_pass_rate") or {}
    assertions = {aid: {"baseline": b_rates.get(aid), "candidate": c_rates.get(aid),
                        "delta": _delta(b_rates.get(aid), c_rates.get(aid))}
                  for aid in sorted(set(b_rates) | set(c_rates))}  # fmt: skip
    dropped = [k for k in ("pass_rate", "mean_accuracy")
               if (d := deltas[k]) is not None and d < -tolerance - EPS]  # fmt: skip
    dropped += [f"assertion {aid}" for aid, a in assertions.items()
                if a["delta"] is not None and a["delta"] < -tolerance - EPS]  # fmt: skip
    rose = [k for k in ("pass_rate", "mean_accuracy")
            if (d := deltas[k]) is not None and d > tolerance + EPS]  # fmt: skip
    mismatch = {k: {"baseline": base.get(k), "candidate": cand.get(k)}
                for k in PINNED if base.get(k) != cand.get(k)}  # fmt: skip
    return {
        "scenario_key": base["scenario_key"],
        "status": "regressed" if dropped else "improved" if rose else "unchanged",
        "regressions": dropped,
        "comparable": not mismatch,
        "version_mismatch": mismatch,
        "baseline": {k: base["stats"].get(k) for k in _SHOWN},
        "candidate": {k: cand["stats"].get(k) for k in _SHOWN},
        "deltas": deltas,
        "assertions": assertions,
    }


_SHOWN = ("runs", "pass_rate", "mean_accuracy", "variance", "pass_variance",
          "mean_agent_actions", "mean_duration_seconds")  # fmt: skip


def compare(baseline: Any, candidate: Any, *, tolerance: float = 0.0,
            fail_on_missing: bool = False) -> dict[str, Any]:  # fmt: skip
    base, cand = _check(baseline, "baseline"), _check(candidate, "candidate")
    b_build, c_build = base["build"]["id"], cand["build"]["id"]
    names = (b_build, c_build) if b_build != c_build else ("baseline", "candidate")
    b_sc = {sc["scenario_key"]: sc for sc in base["scenarios"]}
    c_sc = {sc["scenario_key"]: sc for sc in cand["scenarios"]}
    scenarios = [
        _scenario(b_sc[k], c_sc[k], names, tolerance) for k in sorted(set(b_sc) & set(c_sc))
    ]
    removed = sorted(set(b_sc) - set(c_sc))
    added = sorted(set(c_sc) - set(b_sc))
    regressed = [s["scenario_key"] for s in scenarios if s["status"] == "regressed"]
    reasons = [f"{k}: {', '.join(s['regressions'])} dropped"
               for s in scenarios if (k := s["scenario_key"]) in regressed]  # fmt: skip
    if fail_on_missing and removed:
        reasons.append(f"scenarios missing from the candidate: {', '.join(removed)}")
    return {
        "schema": "mailsim.bench-compare/1",
        "baseline": {"build": b_build, "started_at": base.get("started_at"),
                     "summary": base.get("summary")},  # fmt: skip
        "candidate": {"build": c_build, "started_at": cand.get("started_at"),
                      "summary": cand.get("summary")},  # fmt: skip
        "tolerance": tolerance,
        "summary": {
            "pass_rate_delta": _delta((base.get("summary") or {}).get("pass_rate"),
                                      (cand.get("summary") or {}).get("pass_rate")),
            "mean_accuracy_delta": _delta((base.get("summary") or {}).get("mean_accuracy"),
                                          (cand.get("summary") or {}).get("mean_accuracy")),
            "regressed": regressed,
            "improved": [s["scenario_key"] for s in scenarios if s["status"] == "improved"],
            "unchanged": [s["scenario_key"] for s in scenarios if s["status"] == "unchanged"],
            "not_comparable": [s["scenario_key"] for s in scenarios if not s["comparable"]],
            "added": added,
            "removed": removed,
        },
        "scenarios": scenarios,
        "gate": {"passed": not reasons, "reasons": reasons},
    }  # fmt: skip


__all__ = ["compare"]
