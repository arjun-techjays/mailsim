"""Local no-auth mode: key-less operator on loopback; agent interfaces still need keys."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from mailsim import serve as server
from mailsim.api import local_mode
from mailsim.api.app import create_app
from mailsim.api.local_mode import LocalAccess
from mailsim.auth.principal import Principal, Role
from tests.api.conftest import KEY, FakeEvaluationService

pytestmark = pytest.mark.db


TOKEN = "t" * 43
LOOPBACK = {"base_url": "http://127.0.0.1:8000", "client": ("127.0.0.1", 50000)}


def local_app(api):
    operator = Principal(api.tenant_id, api.project_id, Role.CONTROLLER, key_id="local-operator",
                         local_operator=True)  # fmt: skip
    return create_app(engine=api.engine, session_factory=api.sf, blobs=api.blobs,
                      evaluation_service=FakeEvaluationService(),
                      local_access=LocalAccess(operator, TOKEN))  # fmt: skip


@pytest.fixture()
def local(api):
    client = TestClient(local_app(api), raise_server_exceptions=False, **LOOPBACK)
    client.headers["X-Mailsim-Local-Token"] = TOKEN
    return api, client


def test_keyless_operator_is_controller_and_evaluator(local):
    api, c = local
    me = c.get("/v1/me").json()
    assert me == {"role": "operator", "tenant_id": api.tenant_id, "project_id": api.project_id,
                  "run_id": None, "mailbox_id": None, "key_id": None, "local_no_auth": True}  # fmt: skip
    from tests.api.conftest import scenario_payload

    assert c.post("/v1/scenarios", json=scenario_payload()).status_code == 201
    created = c.post(f"/v1/scenarios/{KEY}/runs", json={}, headers={"Idempotency-Key": "k1"})
    assert created.status_code == 201
    run = created.json()["run"]["id"]
    again = c.post(f"/v1/scenarios/{KEY}/runs", json={}, headers={"Idempotency-Key": "k1"})
    assert again.json()["run"]["id"] == run
    assert c.post(f"/v1/runs/{run}/clock/advance", json={"seconds": 5}).status_code == 200
    assert c.get(f"/v1/runs/{run}/state?at_sequence=1").status_code == 200  # evaluator power
    assert c.get(f"/v1/scenarios/{KEY}/answer-keys").status_code == 200
    assert c.post(f"/v1/runs/{run}/evaluations", json={}).status_code == 201
    boxes = c.get(f"/v1/runs/{run}/mailboxes").json()
    assert c.get(f"/v1/runs/{run}/mailboxes/{boxes[0]['id']}/messages").status_code == 200

    # agent interfaces always demand an agent key, even locally (and even with the token)
    cred = created.json()["agent_credentials"][0]
    mbx = cred["mailbox_id"]
    r = c.get(f"/v1/mailboxes/{mbx}/messages")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    agent = {"Authorization": f"Bearer {cred['api_key']}"}
    assert c.get(f"/v1/mailboxes/{mbx}/messages", headers=agent).status_code == 200
    # a presented key is always verified: no operator fallback for bad or agent keys
    assert c.get(f"/v1/runs/{run}", headers=agent).status_code == 403
    assert c.get("/v1/me", headers=agent).json()["role"] == "agent"
    bad = c.get(f"/v1/runs/{run}", headers={"Authorization": "Bearer not-a-key"})
    assert bad.status_code == 401


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "", "Token msk_x"])
def test_any_authorization_header_is_verified_as_a_key(local, header):
    _, c = local
    r = c.get("/v1/scenarios", headers={"Authorization": header})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x"])
def test_keyless_requests_need_the_launch_token(api, token):
    """A local agent that drops its key is not the operator (answer keys stay out of reach)."""
    c = TestClient(local_app(api), raise_server_exceptions=False, **LOOPBACK)
    headers = {} if token is None else {"X-Mailsim-Local-Token": token}
    for path in ("/v1/me", f"/v1/scenarios/{KEY}/answer-keys", "/v1/runs"):
        r = c.get(path, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED", path
        assert r.headers["X-Mailsim-Local-No-Auth"] == "1"


@pytest.mark.parametrize(("base_url", "client"), [
    ("http://evil.example:8000", ("127.0.0.1", 1)),      # DNS rebinding: foreign Host header
    ("http://127.0.0.1.evil.example", ("127.0.0.1", 1)),
    ("http://127.0.0.1:8000", ("192.168.1.9", 1)),       # non-loopback peer (e.g. 0.0.0.0 bind)
    ("http://127.0.0.1:8000", ("testclient", 1)),
])  # fmt: skip
def test_only_loopback_peers_and_hosts_are_served(api, base_url, client):
    c = TestClient(local_app(api), raise_server_exceptions=False, base_url=base_url, client=client)
    for headers in ({"X-Mailsim-Local-Token": TOKEN}, {"Authorization": "Bearer x"}, {}):
        r = c.get("/v1/me", headers=headers)
        assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(("base_url", "client"), [
    ("http://localhost:8000", ("127.0.0.1", 1)), ("http://[::1]:8000", ("::1", 1)),
    ("http://127.0.0.1", ("::ffff:127.0.0.1", 1)),
])  # fmt: skip
def test_loopback_variants_are_served(api, base_url, client):
    c = TestClient(local_app(api), raise_server_exceptions=False, base_url=base_url, client=client)
    assert c.get("/v1/me", headers={"X-Mailsim-Local-Token": TOKEN}).status_code == 200


def test_loopback_helpers():
    assert local_mode.host_name("[::1]:8000") == "::1" and local_mode.host_name("[::1") == "[::1"
    assert local_mode.host_name("a:80") == "a" and local_mode.host_name("::1") == "::1"
    assert not local_mode.is_loopback(None) and not local_mode.is_loopback("10.0.0.1")
    assert len(local_mode.new_token()) >= 40
    assert not LocalAccess(None, TOKEN).admits(None)  # type: ignore[arg-type]


def test_operator_never_gets_an_agent_facade(local):
    from fastapi import Request

    from mailsim.api.routers import _common
    from mailsim.domain.errors import PermissionDenied

    api, _ = local
    op = Principal(api.tenant_id, api.project_id, Role.CONTROLLER, local_operator=True)
    request = Request({"type": "http", "app": local_app(api), "headers": []})
    with pytest.raises(PermissionDenied):
        _common.agent(request, op)


def test_without_the_mode_keys_are_required(api):
    assert api.client.get("/v1/me").status_code == 401


def test_serve_refuses_non_loopback_binding():
    calls = []
    env: dict[str, str] = {}
    with pytest.raises(SystemExit, match="refuses to bind"):
        server.serve_command("0.0.0.0", 8000, True, lambda h, p: calls.append((h, p)), env)  # noqa: S104
    with pytest.raises(SystemExit):
        server.serve_command("192.168.1.5", 8000, False, lambda h, p: calls.append((h, p)),
                             {"MAILSIM_LOCAL_NO_AUTH": "1"})  # fmt: skip
    assert calls == []
    for host in ("127.0.0.1", "::1", "[::1]", "localhost"):
        assert server.serve_command(host, 8000, True, lambda h, p: calls.append((h, p)), {}) == 0
    assert server.serve_command("0.0.0.0", 9, False, lambda h, p: calls.append((h, p)), {}) == 0  # noqa: S104
    assert len(calls) == 5


def test_local_operator_project_resolution(api, session_factory, capsys, db_engine, tmp_path):
    op = server.local_operator(session_factory, {"MAILSIM_LOCAL_PROJECT_ID": api.project_id})
    assert op.local_operator and op.project_id == api.project_id and op.role is Role.CONTROLLER
    with pytest.raises(SystemExit, match="does not exist"):
        server.local_operator(session_factory, {"MAILSIM_LOCAL_PROJECT_ID": "prj_nope"})
    with pytest.raises(SystemExit, match="several projects"):
        server.local_operator(session_factory, {})  # the shared test DB has many projects
    url = db_engine.url.render_as_string(hide_password=False)
    state = server.build_state({"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path),
                                "MAILSIM_LOCAL_NO_AUTH": "1",
                                "MAILSIM_LOCAL_PROJECT_ID": api.project_id})  # fmt: skip
    try:
        access = state["local_access"]
        assert access.operator.project_id == api.project_id
        err = capsys.readouterr().err
        assert "LOCAL NO-AUTH MODE" in err
        assert f"http://127.0.0.1:5173/#local_token={access.token}" in err
    finally:
        state["db_engine"].dispose()
    state = server.build_state({"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path),
                                "MAILSIM_LOCAL_NO_AUTH": "1", "MAILSIM_DASHBOARD_URL": "http://localhost:4000/",
                                "MAILSIM_LOCAL_PROJECT_ID": api.project_id})  # fmt: skip
    try:
        assert (
            f"http://localhost:4000/#local_token={state['local_access'].token}"
            in capsys.readouterr().err
        )
    finally:
        state["db_engine"].dispose()


def test_local_operator_bootstraps_an_empty_database(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mailsim.store.db import run_migrations
    from mailsim.store.models import ApiKey, Project, Tenant
    from tests.conftest import TEST_DB, recreate_database

    url = recreate_database(f"{TEST_DB}_local")
    run_migrations(url)
    eng = create_engine(url)
    sf = sessionmaker(eng, expire_on_commit=False)
    try:
        op = server.local_operator(sf, {})
        again = server.local_operator(sf, {})
        assert op.project_id == again.project_id
        with sf.begin() as s:
            assert s.get(Project, op.project_id).name == "local"
            s.execute(delete(ApiKey))
            s.execute(delete(Project))
            s.execute(delete(Tenant))
    finally:
        eng.dispose()
