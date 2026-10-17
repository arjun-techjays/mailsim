"""REST API test fixtures: a real app over real PostgreSQL, one fresh tenant per test."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from mailsim.api.app import create_app
from mailsim.api.auth import bootstrap
from mailsim.engine.core import SimEngine
from mailsim.evaluation.contract import (
    Accuracy,
    BuildComparison,
    Efficiency,
    EvaluationReport,
    TrialStats,
    Verdict,
)
from mailsim.store.blob import FilesystemBlobStore
from tests.scenarios import EXAMPLE_DIR, write_variant

KEY = "finance.invoice-bank-change"


def scenario_payload(path: Path = EXAMPLE_DIR / "scenario.yaml") -> dict[str, Any]:
    """Scenario YAML text + its fixtures as base64, as a client would POST them."""
    doc = yaml.safe_load(path.read_text())
    fixtures = {}
    for mb in doc["scenario"]["mailboxes"]:
        for msg in mb.get("initial_messages", []):
            rel = msg["fixture"]
            fixtures[rel] = base64.b64encode((path.parent / rel).read_bytes()).decode()
    return {"source": path.read_text(), "fixtures": fixtures}


def open_agent(s: dict[str, Any]) -> None:
    """Every agent operation allowed (contract-test style)."""
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500


def report(run_id: str, evaluation_id: str = "evl_1", **kw: Any) -> EvaluationReport:
    data: dict[str, Any] = {
        "evaluation_id": evaluation_id, "run_id": run_id, "scenario_key": KEY,
        "scenario_version": 1, "answer_key_version": 2, "answer_key_hash": "h",
        "agent_build": None, "checks_enabled": True, "checks_source": "default",
        "verdict": Verdict.PASSED, "outcomes": [], "metrics": {}, "accuracy": Accuracy(),
        "efficiency": Efficiency(), "created_at": datetime(2026, 10, 15, tzinfo=UTC),
    }  # fmt: skip
    data.update(kw)
    return EvaluationReport(**data)


@dataclass
class FakeEvaluationService:
    """Implements the frozen EvaluationService interface; records every call."""

    calls: list[tuple[str, Any]] = field(default_factory=list)

    def evaluate(self, scope, run_id, *, answer_key_version, checks, complete_run):
        self.calls.append(("evaluate", (scope, run_id, answer_key_version, checks, complete_run)))
        return report(run_id, f"evl_{len(self.calls)}")

    def list(self, scope, run_id):
        self.calls.append(("list", (scope, run_id)))
        return [report(run_id)]

    def latest(self, scope, run_id):
        self.calls.append(("latest", (scope, run_id)))
        return report(run_id)

    def accuracy(self, scope, scenario_key, *, answer_key_version, builds, baseline):
        self.calls.append(("accuracy", (scope, scenario_key, answer_key_version, builds, baseline)))
        stats = TrialStats(runs=1, mean_accuracy=1.0, variance=None, stdev=None, pass_rate=1.0)
        return BuildComparison(scenario_key=scenario_key, answer_key_version=2,
                               builds={b: stats for b in builds or []}, baseline=baseline)  # fmt: skip

    def report_blob(self, scope, evaluation_id, fmt):
        self.calls.append(("report_blob", (scope, evaluation_id, fmt)))
        return f"<{fmt}:{evaluation_id}>".encode()


class Api:
    """One tenant + project with controller/evaluator keys, over a real app."""

    def __init__(self, session_factory, tmp_path: Path, *, evaluation_service=None) -> None:
        self.sf = session_factory
        self.blobs = FilesystemBlobStore(tmp_path / "blobs")
        self.engine = SimEngine(session_factory, self.blobs)
        self.evaluations = evaluation_service
        self.app = create_app(engine=self.engine, session_factory=session_factory,
                              blobs=self.blobs, evaluation_service=evaluation_service)  # fmt: skip
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.tmp_path = tmp_path
        with session_factory.begin() as s:
            boot = bootstrap(s, "tenant", "project")
        self.tenant_id, self.project_id = boot["tenant_id"], boot["project_id"]
        self.keys = {"controller": boot["controller_key"], "evaluator": boot["evaluator_key"]}

    @staticmethod
    def auth(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    def h(self, role: str) -> dict[str, str]:
        return self.auth(self.keys[role])

    def call(self, method: str, url: str, role: str | None = "controller", *, key: str | None = None,
             expect: int | None = None, **kw: Any):  # fmt: skip
        headers = dict(kw.pop("headers", {}))
        if key is not None:
            headers.update(self.auth(key))
        elif role is not None:
            headers.update(self.h(role))
        r = self.client.request(method, url, headers=headers, **kw)
        if expect is not None:
            assert r.status_code == expect, (method, url, r.status_code, r.text)
        return r

    def publish(self, mutate=None, *, tmp: Path | None = None) -> dict[str, Any]:
        path = (
            write_variant(tmp or self.tmp_path, mutate) if mutate else EXAMPLE_DIR / "scenario.yaml"
        )
        return self.call("POST", "/v1/scenarios", json=scenario_payload(path), expect=201).json()

    def create_run(self, *, start: bool = True, **body: Any) -> dict[str, Any]:
        return self.call("POST", f"/v1/scenarios/{KEY}/runs", json={"start": start, **body},
                         expect=201).json()  # fmt: skip

    def agent(self, created: dict[str, Any]) -> tuple[str, str]:
        cred = created["agent_credentials"][0]
        return cred["api_key"], cred["mailbox_id"]


@pytest.fixture()
def api(session_factory, tmp_path) -> Api:
    return Api(session_factory, tmp_path, evaluation_service=FakeEvaluationService())


@pytest.fixture()
def bare_api(session_factory, tmp_path) -> Api:
    """No evaluation service configured: evaluation routes answer 501."""
    return Api(session_factory, tmp_path)


@pytest.fixture()
def other_api(session_factory, tmp_path) -> Api:
    """A second, unrelated tenant on the same app database."""
    return Api(session_factory, tmp_path / "other", evaluation_service=FakeEvaluationService())


@pytest.fixture()
def eval_api(session_factory, tmp_path) -> Api:
    """The real EvaluationService behind the routes (end-to-end grading over HTTP)."""
    from mailsim.evaluation.service import EvaluationService

    api = Api(session_factory, tmp_path)
    api.evaluations = EvaluationService(api.engine, session_factory, api.blobs)
    api.app = create_app(engine=api.engine, session_factory=session_factory, blobs=api.blobs,
                         evaluation_service=api.evaluations)  # fmt: skip
    api.client = TestClient(api.app, raise_server_exceptions=False)
    return api
