"""Frozen evaluator contract (Wave 0).

Lifecycle: the agent runs against a scenario that contains no grading information. After
the run, an evaluation applies one answer-key version to the run's RECORDED event log and
state (never during the run). Any past run can be re-evaluated later with another key
version or with checks switched on/off, without re-running the agent.

Report shapes below are what REST (`/v1/runs/{id}/evaluations`), CI exports and the dashboard
consume. Pure decision rules live in `rules.py`; check plugins register via `registry.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105 - a status, not a credential
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Verdict(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"  # nothing applicable was decidable; the run is not completed


CATEGORIES = ("task", "safety", "integration", "efficiency", "communication")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(_M):
    kind: Literal["event", "action", "message", "state", "llm"]
    ref: str  # event sequence, action id, message id, state path, llm call id
    note: str | None = None


class AssertionDiff(_M):
    """Expected (from the answer key) versus actual (from the recorded run)."""

    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    matched: bool | None = None  # None when not compared (N/A, inconclusive)
    explanation: str = ""


class AssertionOutcome(_M):
    assertion_id: str
    type: str
    category: str
    severity: Literal["mandatory", "advisory"]
    subject: Literal["agent", "simulator"]
    status: Status
    reason: str = ""
    diff: AssertionDiff = Field(default_factory=AssertionDiff)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    score: float | None = None  # semantic judges only (0..1); never decides a hard failure


class CategoryMetrics(_M):
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0
    not_applicable: int = 0
    accuracy: float | None = None  # passed / (passed + failed); None when nothing decided


class Efficiency(_M):
    agent_actions: int = 0
    denied_actions: int = 0
    replies_sent: int = 0
    virtual_duration_seconds: float = 0.0
    model_input_tokens: int = 0
    model_output_tokens: int = 0


class Accuracy(_M):
    overall: float | None = None  # agent-subject assertions, all severities
    mandatory: float | None = None
    by_category: dict[str, float | None] = Field(default_factory=dict)
    decided: int = 0
    passed: int = 0


class EvaluationReport(_M):
    """One evaluation of one run against one answer-key version."""

    schema_version: Literal["1.0"] = "1.0"
    evaluation_id: str
    run_id: str
    scenario_key: str
    scenario_version: int
    answer_key_version: int | None
    answer_key_hash: str | None
    agent_build: str | None
    checks_enabled: bool
    checks_source: Literal["request", "run", "answer_key", "default"]
    verdict: Verdict
    verdict_reasons: list[str] = Field(default_factory=list)
    outcomes: list[AssertionOutcome]
    metrics: dict[str, CategoryMetrics]  # keyed by CATEGORIES
    accuracy: Accuracy
    efficiency: Efficiency
    reproducibility: dict[str, Any] = Field(
        default_factory=dict
    )  # seed, events, state hash, models
    created_at: datetime


class TrialStats(_M):
    runs: int
    mean_accuracy: float | None
    variance: float | None  # sample variance (n-1); None for n < 2
    stdev: float | None
    pass_rate: float | None  # share of runs with verdict PASSED
    per_assertion_pass_rate: dict[str, float | None] = Field(default_factory=dict)


class BuildComparison(_M):
    """Accuracy across repeated trials per agent build, and the deltas between builds."""

    scenario_key: str
    answer_key_version: int
    builds: dict[str, TrialStats]
    baseline: str | None = None
    deltas: dict[str, dict[str, float | None]] = Field(
        default_factory=dict
    )  # build -> metric -> delta
