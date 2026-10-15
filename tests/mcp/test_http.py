"""Streamable HTTP transport (`/mcp`): per-request bearer auth, both protocol eras, and
mounting through the composition root's adapter hook."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mailsim.api.app import create_app
from mailsim.api.auth import InvalidCredential, revoke_key
from mailsim.mcp.http import router
from mailsim.mcp.server import bearer_resolver
from mailsim.serve import mount_adapters
from mailsim.store import models as m
from tests.mcp.conftest import err, ok, tool_names

pytestmark = pytest.mark.db
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}  # fmt: skip
ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer "},
                                     {"Authorization": "Bearer msk_000000000000_" + "x" * 40}])  # fmt: skip
def test_missing_or_invalid_keys_get_401_before_any_mcp_processing(world, headers):
    r = TestClient(world.app).post("/mcp", json=INIT, headers={**ACCEPT, **headers})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_unconfigured_app_answers_501():
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post("/mcp", json=INIT, headers=ACCEPT)
    assert r.status_code == 501 and r.json()["error"]["code"] == "UNSUPPORTED_OPERATION"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_role_scoped_tools_over_http(world, mode):
    agent, ctl, ev = (world.http(r, mode=mode) for r in ("agent", "controller", "evaluator"))
    assert "send_message" in tool_names(agent) and "inject_incoming_email" not in tool_names(agent)
    assert "get_test_results" in tool_names(ev) and "list_messages" not in tool_names(ctl)
    inbox = ok(agent, "list_messages", {"folder": "inbox"})["items"]
    ok(ctl, "inject_incoming_email", {"run_id": world.run, "mailbox_id": world.agent_mb,
                                      "from": "vendor@example.test", "to": ["finance@example.test"],
                                      "subject": "over http"})  # fmt: skip
    after = ok(agent, "list_messages", {"folder": "inbox"})["items"]
    assert len(after) == len(inbox) + 1 and "over http" in {x["subject"] for x in after}
    assert ok(ev, "get_run_status", {"run_id": world.run})["status"] == "RUNNING"
    blocked = err(agent, "send_message", {"to": ["x@real-domain.example"], "subject": "s"})
    assert blocked["code"] == "EXTERNAL_DELIVERY_BLOCKED"
    assert err(agent, "get_test_results", {"run_id": world.run})["code"] == "PERMISSION_DENIED"
    assert err(ctl, "send_message", {"to": ["vendor@example.test"]})["code"] == "PERMISSION_DENIED"


def test_a_revoked_key_stops_working_on_the_next_request(world):
    agent = world.http("agent")
    assert ok(agent, "list_folders")
    with world.sf.begin() as s:
        revoke_key(s, s.query(m.ApiKey).filter_by(run_id=world.run).one().id)
    r = TestClient(world.app).post("/mcp", json=INIT, headers={
        **ACCEPT, "Authorization": f"Bearer {world.keys['agent']}"})  # fmt: skip
    assert r.status_code == 401


def test_unsupported_methods_and_mounting_through_serve(world):
    app = create_app(engine=world.engine, session_factory=world.sf, blobs=world.env.blobs)
    assert "mailsim.mcp.http" in mount_adapters(app)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {world.keys['controller']}"}
    r = client.post("/mcp", json=INIT, headers={**ACCEPT, **auth})
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "mailsim"
    assert "mcp-session-id" not in r.headers  # stateless: credentials are per request
    assert client.get("/mcp", headers={**ACCEPT, **auth}).status_code == 405
    assert client.put("/mcp", headers=auth).status_code == 405
    assert "/mcp" not in client.get("/openapi.json").json()["paths"]
    assert ok(world.http("controller", app=app), "get_run_status", {"run_id": world.run})


def test_bearer_resolver_without_a_request_fails_closed(world):
    resolve = bearer_resolver(world.sf)
    with pytest.raises(InvalidCredential):
        resolve(None)


def test_a_failing_credential_check_is_a_500_without_detail(world, monkeypatch, caplog):
    def broken(_sf):
        def resolve(_request):
            raise RuntimeError("secret detail: postgresql://db-host")

        return resolve

    monkeypatch.setattr("mailsim.mcp.http.bearer_resolver", broken)
    r = TestClient(world.app).post("/mcp", json=INIT, headers={
        **ACCEPT, "Authorization": f"Bearer {world.keys['agent']}"})  # fmt: skip
    assert r.status_code == 500
    assert (
        r.json()["error"]["code"] == "INTERNAL" and r.json()["error"]["message"] == "internal error"
    )
    assert "secret" not in r.text and "secret detail" in caplog.text
