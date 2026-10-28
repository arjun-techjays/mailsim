"""Bench statistics, release gate and the JUnit export of a bench result document.

Per scenario the repeated trials are aggregated with the evaluator's frozen `trial_stats`
(mean accuracy, sample variance/stdev of accuracy, pass rate, per-assertion pass rate), plus
the sample variance of the pass/fail indicator and mean duration / action counts. The gate
fails a scenario whose pass rate is below `min_pass_rate`, or whose accuracy variance is above
`max_variance` (when set). An agent error is a trial that did not pass.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # noqa: S405 - building XML, never parsing untrusted input
from statistics import mean, variance
from typing import Any

from mailsim.evaluation.contract import Accuracy, AssertionOutcome, Status, Verdict
from mailsim.evaluation.reports.junit import MAX_ATTR, clean
from mailsim.evaluation.rules import trial_stats


def _outcomes(trial: dict[str, Any]) -> list[AssertionOutcome]:
    return [AssertionOutcome(assertion_id=aid, type="bench", category="task",
                             severity="advisory", subject="agent", status=Status(status))
            for aid, status in trial["assertions"].items()]  # fmt: skip


def scenario_stats(trials: list[dict[str, Any]]) -> dict[str, Any]:
    stats = trial_stats([
        (Verdict.PASSED if t["passed"] else Verdict.FAILED, Accuracy(overall=t["accuracy"]),
         _outcomes(t)) for t in trials
    ])  # fmt: skip
    flags = [1.0 if t["passed"] else 0.0 for t in trials]
    return {
        **stats.model_dump(),
        "passed": sum(t["passed"] for t in trials),
        "errors": sum(t["outcome"] == "ERROR" for t in trials),
        "pass_variance": variance(flags) if len(flags) >= 2 else None,
        "mean_duration_seconds": mean(t["duration_seconds"] for t in trials) if trials else None,
        "mean_agent_actions": mean(t["agent_actions"] for t in trials) if trials else None,
    }


def _gate(stats: dict[str, Any], config: dict[str, Any]) -> list[str]:
    reasons = []
    rate, floor = stats["pass_rate"], config["min_pass_rate"]
    if rate is None or rate < floor:
        reasons.append(f"pass rate {_pct(rate)} is below the required {_pct(floor)}")
    cap = config.get("max_variance")
    if cap is not None and stats["variance"] is not None and stats["variance"] > cap:
        reasons.append(f"accuracy variance {stats['variance']:.4f} is above {cap:g}")
    return reasons


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def summarize(doc: dict[str, Any]) -> dict[str, Any]:
    """Add per-scenario stats + gate and the overall summary + gate to a bench document."""
    failures = []
    all_trials = []
    for sc in doc["scenarios"]:
        sc["stats"] = scenario_stats(sc["trials"])
        reasons = _gate(sc["stats"], doc["config"])
        sc["gate"] = {"passed": not reasons, "reasons": reasons}
        failures += [{"scenario_key": sc["scenario_key"], "reason": r} for r in reasons]
        all_trials += sc["trials"]
    accs = [t["accuracy"] for t in all_trials if t["accuracy"] is not None]
    doc["summary"] = {
        "scenarios": len(doc["scenarios"]),
        "trials": len(all_trials),
        "passed": sum(t["passed"] for t in all_trials),
        "errors": sum(t["outcome"] == "ERROR" for t in all_trials),
        "pass_rate": (sum(t["passed"] for t in all_trials) / len(all_trials)) if all_trials
        else None,
        "mean_accuracy": mean(accs) if accs else None,
        "accuracy_variance": variance(accs) if len(accs) >= 2 else None,
        "agent_actions": sum(t["agent_actions"] for t in all_trials),
    }  # fmt: skip
    doc["gate"] = {"passed": bool(doc["scenarios"]) and not failures,
                   "min_pass_rate": doc["config"]["min_pass_rate"],
                   "max_variance": doc["config"].get("max_variance"), "failures": failures}  # fmt: skip
    return doc


def render_junit(doc: dict[str, Any]) -> bytes:
    """One <testsuite> per scenario, one <testcase> per trial (failure = not PASSED, error =
    agent error); suite properties carry the reproducibility fields."""
    total = doc["summary"]["trials"]
    errors = doc["summary"]["errors"]
    failed = total - doc["summary"]["passed"] - errors
    root = ET.Element("testsuites", {
        "name": clean(f"mailsim bench {doc['build']['id']}", MAX_ATTR), "tests": str(total),
        "failures": str(failed), "errors": str(errors), "time": f"{doc['duration_seconds']:.3f}",
    })  # fmt: skip
    for sc in doc["scenarios"]:
        trials = sc["trials"]
        n_err = sum(t["outcome"] == "ERROR" for t in trials)
        n_fail = sum(not t["passed"] for t in trials) - n_err
        suite = ET.SubElement(root, "testsuite", {
            "name": clean(f"{sc['scenario_key']} v{sc['scenario_version']}", MAX_ATTR),
            "tests": str(len(trials)), "failures": str(n_fail), "errors": str(n_err),
            "skipped": "0", "timestamp": doc["started_at"],
            "time": f"{sum(t['duration_seconds'] for t in trials):.3f}",
        })  # fmt: skip
        props = ET.SubElement(suite, "properties")
        st = sc["stats"]
        for name, value in (
            ("agent_build", doc["build"]["id"]),
            ("agent_model", doc["build"].get("agent_model") or ""),
            ("scenario_version_id", sc["scenario_version_id"]),
            ("spec_hash", sc["spec_hash"]),
            ("answer_key_version", "" if sc["answer_key_version"] is None
             else str(sc["answer_key_version"])),
            ("simulator_version", doc["simulator"]["version"]),
            ("llm", ":".join(str(v) for v in (doc["llm"].get("provider"),
                                              doc["llm"].get("model")) if v)),
            ("pass_rate", _num(st["pass_rate"])),
            ("mean_accuracy", _num(st["mean_accuracy"])),
            ("accuracy_variance", _num(st["variance"])),
            ("pass_variance", _num(st["pass_variance"])),
            ("gate", "passed" if sc["gate"]["passed"] else "; ".join(sc["gate"]["reasons"])),
        ):  # fmt: skip
            ET.SubElement(props, "property", {"name": name, "value": clean(value, MAX_ATTR)})
        for t in trials:
            case = ET.SubElement(suite, "testcase", {
                "classname": clean(sc["scenario_key"], MAX_ATTR),
                "name": f"trial-{t['trial']}", "time": f"{t['duration_seconds']:.3f}",
            })  # fmt: skip
            if t["outcome"] == "ERROR":
                node = ET.SubElement(case, "error", {"message": clean(t["error"], MAX_ATTR),
                                                     "type": "agent_error"})  # fmt: skip
                node.text = clean(_rounds_text(t))
            elif not t["passed"]:
                node = ET.SubElement(case, "failure", {
                    "message": clean(f"{t['verdict']}: " + "; ".join(t["verdict_reasons"]),
                                     MAX_ATTR), "type": t["verdict"],
                })  # fmt: skip
                node.text = clean("failed assertions: " + ", ".join(t["failed_assertions"]))
            out = ET.SubElement(case, "system-out")
            out.text = clean(json.dumps({k: t[k] for k in (
                "run_id", "evaluation_id", "seed", "state_hash", "event_count", "accuracy",
                "agent_actions", "virtual_duration_seconds", "interfaces", "assertions")},
                sort_keys=True))  # fmt: skip
    ET.indent(root)
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _rounds_text(trial: dict[str, Any]) -> str:
    return "\n".join(f"round {i}: {r.get('error') or 'ok'}\n{r.get('stderr_tail', '')}"
                     for i, r in enumerate(trial["rounds"], 1))  # fmt: skip


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


__all__ = ["render_junit", "scenario_stats", "summarize"]
