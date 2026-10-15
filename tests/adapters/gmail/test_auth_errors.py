"""Credentials (agent keys only, always), error shapes and capability discovery."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from mailsim.adapters.gmail import errors
from mailsim.api.app import create_app
from mailsim.serve import mount_adapters
from tests.adapters.gmail.conftest import BASE, Gm, b64, raw_message

pytestmark = pytest.mark.db


def test_missing_and_invalid_credentials(gm, monkeypatch):
    monkeypatch.setenv("MAILSIM_LOCAL_NO_AUTH", "1")  # never honoured by the Gmail adapter
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "},
                    {"Authorization": "Bearer msk_000000000000_" + "a" * 40},
                    {"Authorization": "Bearer nonsense"}):  # fmt: skip
        r = gm.client.get(BASE + "/profile", headers={"Authorization": ""} | headers)
        body = r.json()["error"]
        assert r.status_code == 401 and body["status"] == "UNAUTHENTICATED"
        assert body["errors"][0]["reason"] == "authError"
        assert r.headers["www-authenticate"].startswith("Bearer")


def test_only_agent_keys(gm):
    for role in ("controller", "evaluator"):
        key = gm.issue(role=role)
        r = gm.client.get(BASE + "/messages", headers={"Authorization": f"Bearer {key}"})
        assert gm.error(r) == (403, "PERMISSION_DENIED", "forbidden")


def test_key_for_another_mailbox_is_denied_by_the_engine(gm):
    key = gm.issue(gm.vendor_mb)
    r = gm.client.get(BASE + "/messages", headers={"Authorization": f"Bearer {key}"})
    assert gm.error(r)[1] == "PERMISSION_DENIED"


def test_ended_run_revokes_the_key(gm):
    gm.ctl.cancel_run(gm.run)
    r = gm.call("GET", "/messages", 401)
    assert r.json()["error"]["status"] == "UNAUTHENTICATED"


def test_run_not_active(env, tmp_path):
    g = Gm(env, tmp_path)
    run = g.ctl.create_run(env.version(tmp_path / "scn" / "scenario.yaml"))  # never started
    mbx = g.ctl.mailbox_id_for(run, "finance_manager")
    g.run = run
    key = g.issue(mbx)
    r = g.client.get(BASE + "/messages", headers={"Authorization": f"Bearer {key}"})
    assert g.error(r) == (409, "RUN_NOT_ACTIVE", "failedPrecondition")
    assert r.json()["error"]["status"] == "FAILED_PRECONDITION"


def test_scenario_permissions_and_budget(env, tmp_path):
    def restricted(s):
        s["evaluation"]["limits"]["max_agent_actions"] = 1  # allowed: read, search, reply, move

    g = Gm(env, tmp_path, restricted)
    r = g.call("POST", "/messages/send", 403, json={"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "x"}))})  # fmt: skip
    assert g.error(r)[1] == "PERMISSION_DENIED"
    gid = g.gid("Invoice INV-001")
    g.post(f"/messages/{gid}/modify", json={"removeLabelIds": ["INBOX"]})  # move: 1st write
    r = g.call("POST", f"/messages/{gid}/modify", 429, json={"addLabelIds": ["INBOX"]})
    assert g.error(r) == (429, "SIMULATION_LIMIT_EXCEEDED", "rateLimitExceeded")
    assert r.json()["error"]["status"] == "RESOURCE_EXHAUSTED"
    assert g.get("/messages")["resultSizeEstimate"] == 2  # reads stay free
    assert g.get("/messages", params={"q": "invoice"})["resultSizeEstimate"] == 2


def test_search_permission_follows_the_scenario(env, tmp_path):
    def no_search(s):
        s["agent"]["allowed_operations"] = ["read"]

    g = Gm(env, tmp_path, no_search)
    assert g.get("/messages", params={"q": "is:unread"})["resultSizeEstimate"] == 2  # filter: read
    r = g.call("GET", "/messages", 403, params={"q": "invoice"})  # free text: engine search
    assert g.error(r)[1] == "PERMISSION_DENIED"


def test_unknown_paths_and_unsupported_features(gm):
    r = gm.call("GET", "/settings/imap", 501)
    assert gm.error(r) == (501, "UNSUPPORTED_OPERATION", "notImplemented")
    gm.call("POST", "/watch", 501)
    gm.call("POST", "/stop", 501)
    gm.call("GET", "/nothing-here", 404)
    gm.call("GET", "/nothing", 404, base="/gmail/v1")
    gm.call("PATCH", "/messages/abc", 404)
    r = gm.call("POST", "/messages/x", 404, base="/upload/gmail/v1/users/me")
    assert r.json()["error"]["status"] == "NOT_FOUND"


def test_internal_errors_are_gmail_shaped(gm, monkeypatch):
    from mailsim.adapters.gmail.service import GmailService

    def boom(self):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(GmailService, "profile", boom)
    r = gm.call("GET", "/profile", 500)
    assert gm.error(r) == (500, "INTERNAL", "backendError")
    assert "secret" not in r.text


def test_unconfigured_server(env):
    app = create_app()
    mount_adapters(app, ["mailsim.adapters.gmail.api"])
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(BASE + "/profile", headers={"Authorization": "Bearer msk_x"})
    assert r.status_code == 501 and r.json()["error"]["details"][0]["metadata"] == {
        "missing": "session_factory"}  # fmt: skip


def test_error_mapping_table():
    r = errors.to_response(HTTPException(405, "nope"))
    assert r.status_code == 501
    r = errors.to_response(HTTPException(418, "teapot"))
    assert r.status_code == 400
    body = errors.gmail_error("CONFLICT", "c", {"n": 1, "s": "x", "l": [1]}).body
    assert b'"metadata":{"l":"[1]","n":"1","s":"x"}' in body
    assert errors.GMAIL_ERRORS["UPSTREAM_UNAVAILABLE"] == (503, "backendError", "UNAVAILABLE")
    assert errors.GMAIL_ERRORS["RATE_LIMITED"][0] == 429
