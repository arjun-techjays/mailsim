"""UX connection routes: run connection details, server-side connection tests, activity
polling, and interface attribution of recorded actions (one check per interface)."""

from __future__ import annotations

import imaplib
import smtplib
from datetime import UTC, datetime, timedelta

import anyio
import pytest

from mailsim import cli
from mailsim.api.auth import hash_key, issue_key, revoke_key
from mailsim.api.inprocess import asgi_request
from mailsim.mcp.server import build_server, key_resolver
from mailsim.protocols.server import ProtocolServers
from mailsim.serve import mount_adapters
from mailsim.store import models as m
from mcp import Client
from tests.api.conftest import KEY, open_agent
from tests.api.test_ux_authoring import FakeListeners
from tests.scenarios import EXAMPLE

pytestmark = pytest.mark.db
AGENT = "finance@example.test"


class Run:
    def __init__(self, api, *, start: bool = True) -> None:
        self.api = api
        created = api.create_run(start=start)
        self.id = created["run"]["id"]
        self.key, self.mbx = api.agent(created)

    def connection(self, role: str = "controller", expect: int = 200):
        return self.api.call("GET", f"/v1/runs/{self.id}/connection", role, expect=expect).json()

    def test(self, interface: str, key: str | None = None, expect: int = 200):
        body = {"interface": interface, "api_key": key or self.key}
        r = self.api.call("POST", f"/v1/runs/{self.id}/connection-tests", json=body,
                          expect=expect)  # fmt: skip
        assert (key or self.key) not in r.text  # the key is never echoed
        return r.json()

    def activity(self, role: str = "evaluator", **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.api.call("GET", f"/v1/runs/{self.id}/activity?{query}", role,
                             expect=200).json()  # fmt: skip

    def interfaces(self) -> list[tuple[str, str, str | None]]:
        return [(a["actor"], a["operation"], a["interface"])
                for a in self.activity(limit=500)["items"]]  # fmt: skip


@pytest.fixture()
def run(api) -> Run:
    api.publish(open_agent)
    return Run(api)


# ---- connection details ---------------------------------------------------------------------
def test_connection_details_without_listeners_or_adapters(run):
    conn = run.connection()
    assert (conn["run_id"], conn["run_status"], conn["mailbox_id"]) == (run.id, "RUNNING", run.mbx)
    assert (conn["mailbox_address"], conn["persona_id"]) == (AGENT, "finance_manager")
    by = {i["interface"]: i for i in conn["interfaces"]}
    assert list(by) == ["rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp"]
    assert by["rest"] == {"interface": "rest", "available": True, "host": None, "port": None,
                          "base_url": "http://testserver/v1", "tls": None,
                          "username": None, "command": None, "note": None}  # fmt: skip
    assert not by["gmail"]["available"] and "not served" in by["gmail"]["note"]
    assert by["mcp_stdio"]["command"] == ["python", "-m", "mailsim.serve", "mcp-stdio"]
    assert "MAILSIM_API_KEY" in by["mcp_stdio"]["note"]
    assert by["imap"]["available"] is False and by["imap"]["username"] == AGENT
    assert by["imap"]["note"] == "IMAP listener is not running (start with serve, see README)"
    [key] = conn["agent_keys"]
    assert not key["revoked"] and key["expires_at"] is None and key["key_id"].startswith("key_")
    assert run.key.startswith(f"msk_{key['prefix']}_") and len(key["prefix"]) == 12
    assert run.key not in str(conn)


def test_connection_details_with_adapters_listeners_and_rotated_keys(api, run):
    mount_adapters(api.app)
    api.app.state.protocols = FakeListeners()
    api.call("POST", f"/v1/runs/{run.id}/agent-credentials", expect=201)
    with api.sf.begin() as s:
        row = s.query(m.ApiKey).filter(m.ApiKey.key_hash == hash_key(run.key)).one()
        row.expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    conn = run.connection()
    by = {i["interface"]: i for i in conn["interfaces"]}
    assert by["graph"]["base_url"] == "http://testserver/graph/v1.0" and by["graph"]["available"]
    assert by["gmail"]["base_url"] == "http://testserver/gmail/v1"
    assert by["mcp_http"]["base_url"] == "http://testserver/mcp"
    assert by["smtp"] == {"interface": "smtp", "available": True, "host": "127.0.0.1",
                          "port": 1587, "tls": "starttls", "username": AGENT, "base_url": None,
                          "command": None, "note": None}  # fmt: skip
    assert [(k["revoked"], k["expires_at"]) for k in conn["agent_keys"]] == [
        (True, "2030-01-01T00:00:00Z"), (False, None)]  # fmt: skip


def test_connection_is_scoped_and_controller_only(api, other_api, run):
    run.connection("evaluator", expect=403)
    other_api.call("GET", f"/v1/runs/{run.id}/connection", expect=404)
    other_api.call("POST", f"/v1/runs/{run.id}/connection-tests",
                   json={"interface": "rest", "api_key": run.key}, expect=404)  # fmt: skip


# ---- connection tests -----------------------------------------------------------------------
def test_rest_connection_test_makes_a_real_call(run):
    res = run.test("rest")
    assert res == {"interface": "rest", "ok": True, "status_code": 200, "error_code": None,
                   "error_message": None, "details": {"folders": 5},
                   "summary": "REST: agent key accepted, 5 folders visible (HTTP 200)"}  # fmt: skip
    assert ("agent", "read", "rest") in run.interfaces()


def test_http_adapter_connection_tests(api, run):
    mount_adapters(api.app)
    gmail = run.test("gmail")
    assert gmail["ok"] and gmail["details"] == {"email_address": AGENT}
    graph = run.test("graph")
    assert graph["ok"] and graph["details"]["folders_returned"] >= 1
    mcp = run.test("mcp_http")
    assert mcp["ok"] and mcp["details"] == {"folders": 5}
    assert mcp["summary"] == ("MCP (HTTP): agent key accepted, 5 folders visible via the "
                              "list_folders tool (HTTP 200)")  # fmt: skip
    seen = {i for _, _, i in run.interfaces()}
    assert {"gmail", "graph", "mcp_http"} <= seen  # every probe is in the run's activity


def test_adapters_not_served(run):
    for name, label in (("gmail", "Gmail API"), ("graph", "Microsoft Graph"),
                        ("mcp_http", "MCP (HTTP)")):  # fmt: skip
        res = run.test(name)
        assert res["ok"] is False and res["summary"] == f"{label} is not served by this server"
        assert res["status_code"] is None


def test_connection_test_reports_engine_refusals(api):
    api.publish(open_agent)
    ready = Run(api, start=False)
    res = ready.test("rest")
    assert res["ok"] is False and res["status_code"] == 409
    assert res["error_code"] == "RUN_NOT_ACTIVE" and res["summary"].startswith("REST: HTTP 409")
    mount_adapters(api.app)
    gmail = ready.test("gmail")
    assert (gmail["ok"], gmail["error_code"]) == (False, "RUN_NOT_ACTIVE")
    graph = ready.test("graph")
    assert (graph["ok"], graph["error_code"]) == (False, "ErrorPreconditionFailed")
    mcp = ready.test("mcp_http")
    assert (mcp["ok"], mcp["error_code"]) == (False, "RUN_NOT_ACTIVE")  # a real tool call


def test_connection_test_key_checks(api, other_api, run):
    def refused(key):
        res = run.test("rest", key=key)
        assert res["ok"] is False and res["status_code"] is None
        return res["error_code"], res["details"]["reason"]

    assert refused("msk_000000000000_" + "x" * 43) == ("UNAUTHENTICATED", "invalid_key")
    assert refused("nope") == ("UNAUTHENTICATED", "invalid_key")
    assert refused(api.keys["controller"]) == ("PERMISSION_DENIED", "not_agent_key")
    assert refused(api.keys["evaluator"]) == ("PERMISSION_DENIED", "not_agent_key")
    second = Run(api)
    res = run.test("imap", key=second.key)
    assert (res["error_code"], res["details"]) == ("PERMISSION_DENIED", {"reason": "other_run"})
    assert second.id not in str(res)
    other_api.publish(open_agent)
    assert refused(Run(other_api).key) == ("UNAUTHENTICATED", "invalid_key")
    with api.sf.begin() as s:
        revoke_key(s, s.query(m.ApiKey).filter(m.ApiKey.key_hash == hash_key(run.key)).one().id)
    res = run.test("rest")
    assert res["summary"] == "REST: the key is unknown, revoked or expired"
    api.call("POST", f"/v1/runs/{run.id}/connection-tests",
             json={"interface": "carrier-pigeon", "api_key": "x"}, expect=400)  # fmt: skip


def test_connection_test_with_a_foreign_project_agent_key_in_same_tenant(api, run):
    project = api.call("POST", "/v1/projects", json={"name": "p2"}, expect=201).json()
    with api.sf.begin() as s:
        key = issue_key(s, tenant_id=api.tenant_id, project_id=project["id"], role="agent",
                        run_id=run.id, mailbox_id=run.mbx).api_key  # fmt: skip
    assert run.test("rest", key=key)["details"]["reason"] == "invalid_key"


def test_connection_test_hides_internal_failures(run, monkeypatch, caplog):
    async def boom(*_a, **_kw):
        raise RuntimeError("secret detail")

    monkeypatch.setattr("mailsim.api.routers.ux.asgi_request", boom)
    res = run.test("rest")
    assert res["ok"] is False and res["summary"] == "REST: internal error"
    assert res["error_code"] == "INTERNAL" and "secret" not in str(res)
    assert "secret detail" in caplog.text


def test_mcp_stdio_and_listener_connection_tests(api, run):
    stdio = run.test("mcp_stdio")
    assert stdio["ok"] and stdio["details"]["command"][-1] == "mcp-stdio"
    imap = run.test("imap")
    assert imap == {"interface": "imap", "ok": False, "status_code": None,
                    "error_code": "LISTENER_DOWN",
                    "error_message": None, "details": {},
                    "summary": "IMAP listener is not running (start with serve, see README)"}  # fmt: skip
    api.app.state.protocols = FakeListeners(result=(False, "RUN_NOT_ACTIVE", "RUN_NOT_ACTIVE: x"))
    smtp = run.test("smtp")
    assert smtp["ok"] is False and smtp["error_code"] == "RUN_NOT_ACTIVE"
    assert smtp["summary"] == "SMTP: authentication refused: RUN_NOT_ACTIVE: x"
    assert smtp["details"] == {"host": "127.0.0.1", "port": 1587, "username": AGENT}
    assert api.app.state.protocols.calls == [(AGENT, "smtp")]


def test_listener_connection_tests_through_real_listeners(api, run):
    servers = ProtocolServers(api.engine, imap_port=0, smtp_port=0, session_factory=api.sf)
    with servers:
        api.app.state.protocols = servers
        imap = run.test("imap")
        assert imap["ok"] and imap["summary"] == f"IMAP: LOGIN as {AGENT} accepted"
        assert imap["details"]["port"] == servers.imap_port
        smtp = run.test("smtp")
        assert smtp["ok"] and smtp["summary"] == f"SMTP: AUTH as {AGENT} accepted"
        assert {("agent", "read", "imap"), ("agent", "read", "smtp")} <= set(run.interfaces())
        assert servers.check_login("someone@else.test", run.key)[0] is False
        api.call("POST", f"/v1/runs/{run.id}/cancel", expect=200)
        ok, code, message = servers.check_login(AGENT, run.key)
        assert (ok, code) == (False, None) and run.key not in message
    assert servers.running is False


def test_check_login_reports_engine_refusals(api):
    api.publish(open_agent)
    ready = Run(api, start=False)
    servers = ProtocolServers(api.engine, imap_port=0, smtp_port=0, session_factory=api.sf)
    ok, code, message = servers.check_login(AGENT, ready.key)
    assert (ok, code) == (False, "RUN_NOT_ACTIVE") and "RUN_NOT_ACTIVE" in message


# ---- activity -------------------------------------------------------------------------------
def test_activity_polling(api, run):
    first = run.activity()
    assert [i["operation"] for i in first["items"]][:2] == ["create_run", "start"]
    assert first["next_after_id"] == first["items"][-1]["id"]
    assert all(i["interface"] == "api" for i in first["items"])
    empty = run.activity(after_id=first["next_after_id"])
    assert empty == {"items": [], "next_after_id": first["next_after_id"]}
    api.call("GET", f"/v1/mailboxes/{run.mbx}/folders", key=run.key, expect=200)
    api.call("GET", f"/v1/mailboxes/{run.mbx}/folders", key=run.key, expect=200)
    page = run.activity("controller", after_id=first["next_after_id"], limit=1)
    [item] = page["items"]
    assert (item["actor"], item["operation"], item["interface"]) == ("agent", "read", "rest")
    assert item["id"] > first["next_after_id"] and item["virtual_time"]
    rest = run.activity(after_id=page["next_after_id"])
    assert len(rest["items"]) == 1
    actions = api.call("GET", f"/v1/runs/{run.id}/actions", expect=200).json()
    assert actions[-1]["interface"] == "rest"
    api.call("GET", f"/v1/runs/{run.id}/activity", key=run.key, expect=403)
    api.call("GET", "/v1/runs/run_missing/activity", expect=404)


# ---- interface attribution ------------------------------------------------------------------
def test_each_http_interface_is_recorded(api, run):
    mount_adapters(api.app)
    auth = {"Authorization": f"Bearer {run.key}"}
    api.client.get("/gmail/v1/users/me/labels", headers=auth).raise_for_status()
    api.client.get("/graph/v1.0/me/mailFolders", headers=auth).raise_for_status()
    r = api.client.post("/mcp", headers={**auth, "Accept": "application/json, text/event-stream",
                                         "mcp-protocol-version": "2025-11-25"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "list_folders", "arguments": {}}})  # fmt: skip
    assert r.status_code == 200 and not r.json()["result"].get("isError"), r.text
    api.call("POST", f"/v1/runs/{run.id}/clock/advance", json={"seconds": 1}, expect=200)
    seen = [(actor, iface) for actor, _op, iface in run.interfaces()]
    for expected in (("agent", "gmail"), ("agent", "graph"), ("agent", "mcp_http"),
                     ("controller", "api")):  # fmt: skip
        assert expected in seen, (expected, seen)


def test_mcp_stdio_tool_calls_are_recorded(api, run):
    server = build_server(api.engine, key_resolver(api.sf, run.key), interface="mcp_stdio")

    async def go():
        async with Client(server) as client:
            return await client.call_tool("list_folders", {})

    assert not anyio.run(go).is_error
    assert ("agent", "mcp_stdio") in [(a, i) for a, _o, i in run.interfaces()]


def test_run_stdio_attributes_mcp_stdio(monkeypatch):
    from mailsim.mcp import stdio

    seen = {}

    def fake_build(engine, resolver, **kw):
        seen.update(kw)
        return object()

    monkeypatch.setattr(stdio, "verify_principal", lambda sf, key: None)
    monkeypatch.setattr(stdio, "build_server", fake_build)
    monkeypatch.setattr(stdio.anyio, "run", lambda *a: None)
    stdio.run_stdio(object(), "msk_x", session_factory=object())
    assert seen["interface"] == "mcp_stdio"


def test_imap_and_smtp_sessions_are_recorded(api, run):
    with ProtocolServers(api.engine, imap_port=0, smtp_port=0, session_factory=api.sf) as srv:
        imap = imaplib.IMAP4("127.0.0.1", srv.imap_port, timeout=10)
        imap.login(AGENT, run.key)
        imap.select("INBOX")
        imap.logout()
        smtp = smtplib.SMTP("127.0.0.1", srv.smtp_port, timeout=10)
        smtp.login(AGENT, run.key)
        smtp.sendmail(AGENT, ["vendor@example.test"],
                      b"From: finance@example.test\r\nTo: vendor@example.test\r\n"
                      b"Subject: hi\r\n\r\nbody\r\n")  # fmt: skip
        smtp.quit()
    seen = run.interfaces()
    assert ("agent", "send", "smtp") in seen
    assert ("agent", "read", "imap") in seen


def test_cli_commands_are_recorded(db_engine, tmp_path, monkeypatch, capsys, session_factory):
    import json

    monkeypatch.setenv("MAILSIM_DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))

    def run_cli(*argv):
        assert cli.main(list(argv)) == 0
        return json.loads(capsys.readouterr().out)

    project = run_cli("project-create")["project_id"]
    version = run_cli("publish", str(EXAMPLE), "--project", project)["scenario_version_id"]
    run_id = run_cli("run-create", version, "--project", project)["run_id"]
    with session_factory() as s:
        rows = s.query(m.ActionRow).filter(m.ActionRow.run_id == run_id).all()
    assert rows and {r.interface for r in rows} == {"cli"}


# ---- in-process requests --------------------------------------------------------------------
def test_asgi_request_caps_the_response_body(api):
    async def go():
        return await asgi_request(api.app, "GET", "/v1/health", max_body=5)

    res = anyio.run(go)
    assert res.status == 200 and res.truncated and len(res.body) == 5
    assert res.headers["content-type"] == "application/json"


def test_asgi_request_posts_a_body_and_sees_disconnects(api):
    async def go():
        return await asgi_request(api.app, "POST", "/v1/tools/yaml-to-json", body=b'{"source": 1}',
                                  headers={"content-type": "application/json"})  # fmt: skip

    res = anyio.run(go)
    assert res.status == 401  # no key: the request went through the auth stack


def test_scenario_answer_keys_publishable_by_evaluators(api):
    api.publish()
    created = api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", "evaluator",
                       json={"source": "answer_key_version: '1.0'\nanswer_key:\n  scenario: "
                                       f"{KEY}\n  version: 5\n"}, expect=201).json()  # fmt: skip
    assert created["version"] == 5


def test_expired_agent_keys_are_listed_with_their_expiry(api, run):
    with api.sf.begin() as s:
        row = s.query(m.ApiKey).filter(m.ApiKey.key_hash == hash_key(run.key)).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    assert run.connection()["agent_keys"][0]["expires_at"]
    assert run.test("rest")["details"]["reason"] == "invalid_key"


def test_connection_test_with_a_non_json_answer(run, monkeypatch):
    from mailsim.api.inprocess import InProcessResponse

    async def html(*_a, **_kw):
        return InProcessResponse(status=200, headers={}, body=b"<html>")

    monkeypatch.setattr("mailsim.api.routers.ux.asgi_request", html)
    res = run.test("rest")
    assert res["ok"] is False and res["summary"] == "REST: HTTP 200" and res["details"] == {}


def test_asgi_request_handles_repeated_receives_and_other_messages():
    seen = []

    async def app(scope, receive, send):
        seen.append(await receive())
        seen.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.trailers"})  # ignored
        await send({"type": "http.response.body", "body": b""})

    res = anyio.run(lambda: asgi_request(app, "PUT", "/x", body=b"abc"))
    assert res.status == 204 and res.body == b""
    assert [m["type"] for m in seen] == ["http.request", "http.disconnect"]
    assert seen[0]["body"] == b"abc"


def test_interface_middleware_passes_other_scopes_through():
    from mailsim.api.interface import InterfaceAttribution, interface_for_path
    from mailsim.engine.context import current_interface

    seen = []

    async def app(scope, receive, send):
        seen.append(current_interface.get())

    anyio.run(lambda: InterfaceAttribution(app)({"type": "lifespan"}, None, None))
    anyio.run(lambda: InterfaceAttribution(app)({"type": "http", "path": "/gmail/v1/x"}, None,
                                                None))  # fmt: skip
    assert seen == [None, "gmail"]
    assert [interface_for_path(p) for p in ("/v1/mailboxes/m/x", "/mcp", "/graph/v1.0/me",
                                            "/v1/runs")] == ["rest", "mcp_http", "graph", "api"]  # fmt: skip


def test_use_interface_rejects_unknown_names_and_restores():
    from mailsim.engine.context import current_interface, use_interface, with_interface

    with pytest.raises(ValueError, match="unknown interface"), use_interface("carrier-pigeon"):
        pass
    with use_interface("cli"):
        assert with_interface("imap", current_interface.get)() == "imap"
        assert current_interface.get() == "cli"
    assert current_interface.get() is None


def test_connection_test_results_never_echo_the_key(api, run):
    api.app.state.protocols = FakeListeners(result=(False, "X", f"bad key {run.key}"))
    res = run.test("imap")
    assert res["error_message"] == "bad key [redacted]" and res["summary"].endswith("[redacted]")


@pytest.mark.parametrize(("interface", "code"), [("imap", "AUTHENTICATIONFAILED"),
                                                 ("smtp", "AUTHENTICATION_FAILED")])  # fmt: skip
def test_listener_credential_failures_use_the_protocol_code(api, run, interface, code):
    api.app.state.protocols = FakeListeners(result=(False, None, "authentication failed"))
    res = run.test(interface)
    assert (res["ok"], res["error_code"]) == (False, code)


def test_connection_tests_never_log_the_key(run, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        for interface in ("rest", "mcp_stdio", "imap"):
            run.test(interface)
        run.test("rest", key=run.key + "x")
    assert caplog.records and run.key not in caplog.text


def test_whitespace_only_keys_are_rejected_by_the_schema(api, run):
    r = api.call("POST", f"/v1/runs/{run.id}/connection-tests",
                 json={"interface": "rest", "api_key": "   "}, expect=400)  # fmt: skip
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert run.test("rest", key=f"  {run.key}  ")["ok"]  # surrounding whitespace is stripped


def test_mcp_paths_are_matched_exactly():
    from mailsim.api.interface import interface_for_path

    assert [interface_for_path(p) for p in ("/mcp", "/mcp/x", "/mcpx", "/mcp-tools")] == [
        "mcp_http", "mcp_http", "api", "api"]  # fmt: skip


def test_mcp_probe_payload_unwrapping():
    from mailsim.api.routers.ux import _mcp_payload

    rpc_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "nope"}}
    assert _mcp_payload(rpc_error) is rpc_error
    assert _mcp_payload(None) is None and _mcp_payload([1]) == [1]
    assert _mcp_payload({"jsonrpc": "2.0", "id": 1, "result": "odd"}) is None
    tool_error = {"error": {"code": "RUN_NOT_ACTIVE", "message": "m", "details": {}}}
    wrapped = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"isError": True, "structuredContent": tool_error},
    }
    assert _mcp_payload(wrapped) == tool_error
