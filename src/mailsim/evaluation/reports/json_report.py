"""JSON export: the frozen EvaluationReport shape, verbatim."""

from __future__ import annotations

from mailsim.evaluation.contract import EvaluationReport


def render_json(report: EvaluationReport) -> bytes:
    return report.model_dump_json(indent=2).encode()
