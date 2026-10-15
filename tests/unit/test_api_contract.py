"""Frozen REST contract: error envelope, HTTP mapping, OpenAPI document."""

import json

from fastapi.testclient import TestClient

from mailsim.api import openapi
from mailsim.api.app import create_app
from mailsim.api.errors import HTTP_STATUS
from mailsim.domain.errors import ErrorCode, InvalidRequest
from mailsim.llm.contract import DEFAULT_MODEL, Budget, LLMRequest, LLMResponse, Message
from tests.scenarios import ROOT


def client(**state):
    return TestClient(create_app(**state), raise_server_exceptions=False)


def test_every_error_code_has_an_http_status():
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert HTTP_STATUS[ErrorCode.EXTERNAL_DELIVERY_BLOCKED] == 422
    assert HTTP_STATUS[ErrorCode.UNSUPPORTED_OPERATION] == 501


def test_health_and_state():
    c = client(engine="E")
    r = c.get("/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert c.app.state.engine == "E"


def test_unauthenticated_requests_fail_with_envelope():
    r = client().get("/v1/runs/run_x", headers={"x-request-id": "req-123"})
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "UNAUTHENTICATED" and body["details"] == {}
    assert body["request_id"] == "req-123" and r.headers["x-request-id"] == "req-123"
    bad = client().get("/v1/runs/run_x", headers={"x-request-id": "evil value;injected"})
    assert bad.json()["error"]["request_id"] != "evil value;injected"
    assert len(bad.json()["error"]["request_id"]) == 32


def _frozen_routes():
    from mailsim.api.routers import control, mailbox, simulator

    for module in (control, simulator, mailbox):
        yield from module.router.routes


def test_every_frozen_route_requires_a_credential():
    """WS-A implemented the routes; every one of them refuses a missing credential (401)."""
    c = client()
    checked = 0
    for route in _frozen_routes():
        url = route.path.replace("{", "").replace("}", "")
        for method in sorted(route.methods - {"HEAD"}):
            r = c.request(method, url, json={})
            assert r.status_code == 401, (method, route.path, r.text)
            assert r.json()["error"]["code"] == "UNAUTHENTICATED"
            checked += 1
    assert checked >= 28


def test_validation_unknown_route_and_method_errors():
    from pydantic import BaseModel

    app = create_app()

    class Body(BaseModel):
        seconds: int

    @app.post("/v1/_validate")
    def validate(body: Body) -> dict[str, int]:
        return {"ok": body.seconds}

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/v1/_validate", json={"seconds": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    assert r.json()["error"]["details"]["errors"]
    r = c.get("/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = c.delete("/v1/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
    assert len(r.json()["error"]["request_id"]) == 32


def test_sim_errors_and_unexpected_errors_map_to_envelope():
    app = create_app()

    @app.get("/v1/_boom/sim")
    def sim():
        raise InvalidRequest("bad", details={"x": 1})

    @app.get("/v1/_boom/crash")
    def crash():
        raise RuntimeError("secret internals")

    @app.get("/v1/_boom/teapot")
    def teapot():
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="teapot")

    @app.get("/v1/_boom/auth")
    def auth():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=401, detail="no key", headers={"WWW-Authenticate": "Bearer"}
        )

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/v1/_boom/sim")
    assert r.status_code == 400 and r.json()["error"]["details"] == {"x": 1}
    r = c.get("/v1/_boom/crash")
    assert r.status_code == 500 and r.json()["error"] == {
        "code": "INTERNAL", "message": "internal error", "details": {},
        "request_id": r.json()["error"]["request_id"]}  # fmt: skip
    assert "secret" not in r.text
    assert c.get("/v1/_boom/teapot").json()["error"]["code"] == "INVALID_REQUEST"
    auth_resp = c.get("/v1/_boom/auth")
    assert auth_resp.json()["error"]["code"] == "UNAUTHENTICATED"
    assert auth_resp.headers["www-authenticate"] == "Bearer"


def test_openapi_document_is_frozen(tmp_path):
    committed = (ROOT / "openapi" / "openapi.json").read_text()
    assert openapi.render() == committed, "run: python -m mailsim.api.openapi to update"
    doc = json.loads(committed)
    paths = set(doc["paths"])
    for p in ("/v1/scenarios/{scenario_key}/runs", "/v1/runs/{run_id}/mailboxes/{mailbox_id}/inject",
              "/v1/mailboxes/{mailbox_id}/messages/{message_id}/reply", "/v1/runs/{run_id}/evaluations",
              "/v1/scenarios/{scenario_key}/answer-keys", "/v1/scenarios/{scenario_key}/accuracy"):  # fmt: skip
        assert p in paths
    target = tmp_path / "o.json"
    assert openapi.main([str(target)]) == 0 and target.read_text() == committed


def test_llm_contract_models():
    req = LLMRequest(purpose="persona", messages=[Message(role="user", content="hi")])
    assert req.max_tokens == 1024 and Budget().max_calls == 200
    assert LLMResponse(text="x", provider="stub", model=DEFAULT_MODEL).model == "claude-sonnet-5-5"


def test_every_route_declares_exactly_one_role_dependency():
    from mailsim.auth.principal import Role

    evaluator_only = ("/evaluations", "/results", "/accuracy")
    for route in _frozen_routes():
        roles = [getattr(d.call, "mailsim_roles", None) for d in route.dependant.dependencies]
        roles = [r for r in roles if r is not None]
        assert len(roles) == 1, route.path
        if route.path.startswith("/v1/mailboxes/"):
            assert roles[0] == {Role.AGENT}, route.path
        else:
            assert Role.AGENT not in roles[0], f"{route.path} must not accept agent credentials"
        if any(x in route.path for x in evaluator_only):
            assert roles[0] == {Role.EVALUATOR}, route.path
        if route.path.endswith("/answer-keys") and "GET" in route.methods:
            assert roles[0] == {Role.EVALUATOR}, "answer keys are hidden from non-evaluators"


def test_role_dependencies_enforce_roles():
    import pytest

    from mailsim.api.deps import agent_for_mailbox, require_role
    from mailsim.auth.principal import Principal, Role
    from mailsim.domain.errors import PermissionDenied

    ctl = Principal("t", "p", Role.CONTROLLER)
    agent = Principal("t", "p", Role.AGENT, "run_1", "mbx_1")
    dep = require_role(Role.CONTROLLER)
    assert dep(ctl) is ctl
    with pytest.raises(PermissionDenied):
        dep(agent)
    assert agent_for_mailbox("mbx_1", agent) is agent
    with pytest.raises(PermissionDenied):
        agent_for_mailbox("mbx_2", agent)
    with pytest.raises(PermissionDenied):
        agent_for_mailbox("mbx_1", ctl)
