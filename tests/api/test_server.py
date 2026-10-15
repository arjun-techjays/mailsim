"""Composition root, CLI (bootstrap/serve), key helpers and router plumbing."""

from __future__ import annotations

import importlib.machinery
import json
import os
import stat
import sys
import types

import pytest
from fastapi.testclient import TestClient

from mailsim import serve as server
from mailsim.api import auth
from mailsim.api.app import create_app
from mailsim.api.routers import _common
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import InvalidRequest, PermissionDenied

pytestmark = pytest.mark.db


@pytest.fixture()
def env(db_engine, tmp_path, monkeypatch):
    url = db_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("MAILSIM_DATABASE_URL", url)
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))
    return {"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path / "blobs")}


def test_bootstrap_cli_then_serve_the_app_from_env(env, capsys, monkeypatch):
    migrated = []
    monkeypatch.setattr(server, "run_migrations", lambda: migrated.append(True))
    assert server.main(["bootstrap", "--tenant", "acme", "--project", "agents", "--migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert migrated == [True] and out["controller_key"].startswith("msk_")
    app = server.app_from_env(env)
    assert app.state.engine is not None and app.state.blobs.root.name == "blobs"
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"/v1/projects/{out['project_id']}",
              headers={"Authorization": f"Bearer {out['evaluator_key']}"})  # fmt: skip
    assert r.status_code == 200 and r.json()["name"] == "agents"
    app.state.db_engine.dispose()


def test_bootstrap_output_file_is_private(env, capsys, tmp_path):
    target = tmp_path / "keys.json"
    target.write_text("old")
    os.chmod(target, 0o644)
    assert server.main(["bootstrap", "--output", str(target)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["keys_written_to"] == str(target) and "msk_" not in json.dumps(printed)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["controller_key"].startswith("msk_")


def test_build_state_reads_body_limit(env):
    state = server.build_state({**env, "MAILSIM_MAX_BODY_BYTES": "123"})
    assert state["max_body_bytes"] == 123
    state["db_engine"].dispose()


def test_bootstrap_cli_reports_errors(env, capsys):
    assert server.main(["bootstrap", "--tenant", " "]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "INVALID_REQUEST"


def test_serve_command_uses_the_injected_runner():
    seen = []
    assert server.main(["serve", "--port", "9999"], serve=lambda h, p: seen.append((h, p))) == 0
    assert seen == [("127.0.0.1", 9999)]


def test_evaluation_service_wiring(monkeypatch):
    monkeypatch.setattr(server, "EVALUATION_SERVICE", "mailsim._no_such_module_")
    assert server.evaluation_service(session_factory=1) is None

    class Service:
        def __init__(self, session_factory, blobs=None):
            self.args = (session_factory, blobs)

    class Broken:
        def __init__(self, required_thing):
            raise AssertionError("never called")

    module = types.ModuleType("fake_eval")
    module.__spec__ = importlib.machinery.ModuleSpec("fake_eval", None)
    module.EvaluationService = Service
    monkeypatch.setitem(sys.modules, "fake_eval", module)
    monkeypatch.setattr(server, "EVALUATION_SERVICE", "fake_eval")
    built = server.evaluation_service(session_factory="sf", blobs="b", engine="e")
    assert built.args == ("sf", "b")
    module.EvaluationService = Broken  # present but not constructible: fail fast
    with pytest.raises(TypeError):
        server.evaluation_service(session_factory="sf")


def test_build_state_defaults(monkeypatch, tmp_path, env):
    monkeypatch.chdir(tmp_path)
    state = server.build_state({"MAILSIM_DATABASE_URL": env["MAILSIM_DATABASE_URL"]})
    assert str(state["blobs"].root) == server.DEFAULT_BLOB_DIR
    state["db_engine"].dispose()


def test_issue_key_binding_rules(session_factory):
    with session_factory.begin() as s:
        boot = auth.bootstrap(s, "t", "p")
        with pytest.raises(InvalidRequest):
            auth.issue_key(s, tenant_id=boot["tenant_id"], project_id=boot["project_id"],
                           role=Role.AGENT, run_id="run_x")  # fmt: skip
        with pytest.raises(InvalidRequest):
            auth.issue_key(s, tenant_id=boot["tenant_id"], project_id=boot["project_id"],
                           role="controller", mailbox_id="mbx_1")  # fmt: skip
        principal = auth.verify_key(s, boot["controller_key"])
        assert principal.role == Role.CONTROLLER and principal.key_id.startswith("key_")
        for bad in ("", "msk_zz_" + "a" * 40, boot["controller_key"][:-1] + "~", "x" * 500):
            with pytest.raises(auth.InvalidCredential):
                auth.verify_key(s, bad)


def test_server_without_state_answers_explicitly(session_factory):
    with session_factory.begin() as s:
        key = auth.bootstrap(s, "t", "p")["controller_key"]
    hdr = {"Authorization": f"Bearer {key}"}
    r = TestClient(create_app(), raise_server_exceptions=False).get("/v1/scenarios", headers=hdr)
    assert r.status_code == 501 and r.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
    app = create_app(session_factory=session_factory)  # no engine / blob store configured
    r = TestClient(app, raise_server_exceptions=False).get("/v1/runs/run_x", headers=hdr)
    assert r.status_code == 501 and r.json()["error"]["details"] == {"missing": "engine"}


def test_facade_helpers_refuse_the_wrong_role():
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(engine=object()))
    )
    ev = Principal("t", "p", Role.EVALUATOR, key_id="k")
    with pytest.raises(PermissionDenied):
        _common.controller(request, ev)
    with pytest.raises(PermissionDenied):
        _common.agent(request, ev)
    assert _common.evaluator(request, ev).scope.tenant_id == "t"
