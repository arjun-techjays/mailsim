"""Markdown summary for humans (PR comments, CI job summaries).

Agent-written text can reach reasons and diffs, so table cells are escaped and diffs are
placed in fenced blocks that the content cannot close.
"""

from __future__ import annotations

import json

from mailsim.evaluation.contract import EvaluationReport, Status


def cell(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    text = " ".join(text.split())
    for ch in "<>`*_[]":
        text = text.replace(ch, f"\\{ch}")
    return text[:300]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(report: EvaluationReport) -> bytes:
    r = report
    lines = [
        f"# Evaluation {cell(r.evaluation_id)}: {r.verdict.value}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Run | {cell(r.run_id)} |",
        f"| Scenario | {cell(r.scenario_key)} v{r.scenario_version} |",
        f"| Answer key | v{r.answer_key_version} |",
        f"| Agent build | {cell(r.agent_build or 'n/a')} |",
        f"| Checks | {'on' if r.checks_enabled else 'off'} ({r.checks_source}) |",
        f"| Accuracy | {_pct(r.accuracy.overall)} ({r.accuracy.passed}/{r.accuracy.decided}) |",
        f"| Mandatory accuracy | {_pct(r.accuracy.mandatory)} |",
        f"| Agent actions | {r.efficiency.agent_actions} ({r.efficiency.denied_actions} denied) |",
        f"| Virtual duration | {r.efficiency.virtual_duration_seconds:g}s |",
        "",
    ]
    if r.verdict_reasons:
        lines += ["## Verdict reasons", ""]
        lines += [f"- {cell(x)}" for x in r.verdict_reasons] + [""]
    lines += [
        "## Assertions",
        "",
        "| Assertion | Type | Category | Severity | Status | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for o in r.outcomes:
        lines.append(f"| {cell(o.assertion_id)} | {o.type} | {o.category} | {o.severity} | "
                     f"{o.status.value} | {cell(o.reason)} |")  # fmt: skip
    failed = [o for o in r.outcomes if o.status is Status.FAIL]
    if failed:
        lines += ["", "## Failures"]
        for o in failed:
            diff = json.dumps(o.diff.model_dump(), indent=2, sort_keys=True, default=str)
            fence = "`" * max(3, _longest_backtick_run(diff) + 1)
            lines += ["", f"### {cell(o.assertion_id)}", "", f"{fence}json", diff, fence]
    return ("\n".join(lines) + "\n").encode()


def _longest_backtick_run(text: str) -> int:
    best = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        best = max(best, run)
    return best
