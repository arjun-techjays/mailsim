"""Evaluation routes delegate to the frozen EvaluationService interface (WS-B)."""

from __future__ import annotations

import pytest

from mailsim.store.repos import Scope
from tests.api.conftest import KEY

pytestmark = pytest.mark.db


def test_without_a_service_every_evaluation_route_answers_501(bare_api):
    for method, url in (("POST", "/v1/runs/run_x/evaluations"), ("GET", "/v1/runs/run_x/evaluations"),
                        ("GET", "/v1/runs/run_x/results"), ("GET", f"/v1/scenarios/{KEY}/accuracy"),
                        ("GET", "/v1/runs/run_x/evaluations/evl_1/report")):  # fmt: skip
        r = bare_api.call(method, url, "evaluator", json={} if method == "POST" else None)
        assert r.status_code == 501, url
        assert r.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
        assert r.json()["error"]["details"] == {"owner": "WS-B"}


def test_routes_pass_scope_and_arguments(api):
    scope = Scope(api.tenant_id, api.project_id)
    api.publish()
    run = api.create_run()["run"]["id"]
    calls = api.evaluations.calls
    out = api.call("POST", f"/v1/runs/{run}/evaluations", "evaluator", expect=201,
                   json={"answer_key_version": 2, "checks": "off", "complete_run": False}).json()  # fmt: skip
    assert out["run_id"] == run and calls[-1] == ("evaluate", (scope, run, 2, "off", False))
    listed = api.call("GET", f"/v1/runs/{run}/evaluations", "evaluator", expect=200).json()
    assert [e["evaluation_id"] for e in listed] == ["evl_1"]
    latest = api.call("GET", f"/v1/runs/{run}/results", "evaluator", expect=200).json()
    assert latest["verdict"] == "PASSED" and calls[-1] == ("latest", (scope, run))
    acc = api.call("GET", f"/v1/scenarios/{KEY}/accuracy?answer_key_version=2&builds=a&builds=b"
                   "&baseline=a", "evaluator", expect=200).json()  # fmt: skip
    assert set(acc["builds"]) == {"a", "b"}
    assert calls[-1] == ("accuracy", (scope, KEY, 2, ["a", "b"], "a"))
    r = api.call("GET", f"/v1/runs/{run}/evaluations/evl_1/report?format=junit", "evaluator",
                 expect=200)  # fmt: skip
    assert r.content == b"<junit:evl_1>" and r.headers["content-type"] == "application/xml"
    r = api.call("GET", f"/v1/runs/{run}/evaluations/evl_1/report", "evaluator", expect=200)
    assert r.headers["content-type"] == "application/json"
    r = api.call("GET", f"/v1/runs/{run}/evaluations/evl_9/report", "evaluator")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = api.call("GET", f"/v1/runs/{run}/evaluations/evl_1/report?format=pdf", "evaluator")
    assert r.status_code == 400


def test_service_errors_use_the_envelope(api, monkeypatch):
    api.publish()
    run = api.create_run()["run"]["id"]
    from mailsim.domain.errors import Conflict

    def boom(*a, **k):
        raise Conflict("the run changed after it was evaluated", details={"latest": 9})

    monkeypatch.setattr(api.evaluations, "evaluate", boom)
    r = api.call("POST", f"/v1/runs/{run}/evaluations", "evaluator", json={})
    assert r.status_code == 409 and r.json()["error"]["details"] == {"latest": 9}
