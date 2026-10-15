"""Persistence of evaluations, per-assertion results and report blobs (evaluator-only tables).

Grading details live here and only here: the event log receives nothing but the
`run.completed {verdict, evaluation_id}` event written by the engine on completion.
Every read is scoped through the run's tenant/project (out of scope -> NOT_FOUND).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from mailsim.domain.errors import NotFound
from mailsim.domain.ids import new_control_id
from mailsim.evaluation.contract import EvaluationReport
from mailsim.store import models as m
from mailsim.store.repos import Scope


def save_evaluation(session: Session, report: EvaluationReport, answer_key_id: str | None) -> None:
    session.add(
        m.Evaluation(
            id=report.evaluation_id,
            run_id=report.run_id,
            answer_key_id=answer_key_id,
            checks_enabled=report.checks_enabled,
            verdict=report.verdict.value,
            accuracy=report.accuracy.overall,
            agent_build=report.agent_build,
            report=report.model_dump(mode="json"),
            created_at=report.created_at,
        )
    )
    session.flush()  # the parent row must exist before its assertion results
    for o in report.outcomes:
        session.add(
            m.AssertionResult(
                evaluation_id=report.evaluation_id,
                assertion_id=o.assertion_id,
                run_id=report.run_id,
                status=o.status.value,
                category=o.category,
                severity=o.severity,
                evidence=[e.model_dump(mode="json") for e in o.evidence],
                diff=o.diff.model_dump(mode="json"),
                created_at=report.created_at,
            )
        )


def add_report_blob(session: Session, evaluation_id: str, fmt: str, blob_ref: str) -> None:
    session.add(m.ReportRow(id=new_control_id("rpt"), evaluation_id=evaluation_id, format=fmt,
                            blob_ref=blob_ref))  # fmt: skip


def _scoped(scope: Scope) -> Select[m.Evaluation]:
    return (
        select(m.Evaluation)
        .join(m.Run, m.Run.id == m.Evaluation.run_id)
        .where(m.Run.tenant_id == scope.tenant_id, m.Run.project_id == scope.project_id)
    )


def list_for_run(session: Session, scope: Scope, run_id: str) -> list[EvaluationReport]:
    rows = session.execute(
        _scoped(scope)
        .where(m.Evaluation.run_id == run_id)
        .order_by(m.Evaluation.created_at, m.Evaluation.id)
    ).scalars()
    return [EvaluationReport.model_validate(r.report) for r in rows]


def report_blob_ref(session: Session, scope: Scope, evaluation_id: str, fmt: str) -> str:
    ev = session.execute(
        _scoped(scope).where(m.Evaluation.id == evaluation_id)
    ).scalar_one_or_none()
    if ev is None:
        raise NotFound(f"evaluation {evaluation_id} not found")
    ref = session.scalar(
        select(m.ReportRow.blob_ref)
        .where(m.ReportRow.evaluation_id == ev.id, m.ReportRow.format == fmt)
        .order_by(m.ReportRow.created_at.desc())
        .limit(1)
    )
    if ref is None:
        raise NotFound(f"no {fmt} report for evaluation {evaluation_id}")
    return ref


@dataclass(frozen=True)
class StoredTrial:
    run_id: str
    answer_key_version: int
    agent_build: str | None
    report: EvaluationReport


def trials_for_scenario(session: Session, scope: Scope, scenario_key: str) -> list[StoredTrial]:
    """Stored evaluations of `scenario_key` runs, oldest first."""
    rows = session.execute(
        _scoped(scope)
        .add_columns(m.AnswerKey.version)
        .join(m.AnswerKey, m.AnswerKey.id == m.Evaluation.answer_key_id)
        .join(m.Scenario, m.Scenario.id == m.AnswerKey.scenario_id)
        .where(m.Scenario.key == scenario_key, m.Scenario.tenant_id == scope.tenant_id,
               m.Scenario.project_id == scope.project_id)
        .order_by(m.Evaluation.created_at, m.Evaluation.id)
    ).all()  # fmt: skip
    return [
        StoredTrial(ev.run_id, version, ev.agent_build, EvaluationReport.model_validate(ev.report))
        for ev, version in rows
    ]


__all__ = [
    "StoredTrial",
    "add_report_blob",
    "list_for_run",
    "report_blob_ref",
    "save_evaluation",
    "trials_for_scenario",
]
