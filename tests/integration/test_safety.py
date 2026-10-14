"""AC3: external delivery is impossible. Defence in depth, verified several ways."""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mailsim.domain.errors import ExternalDeliveryBlocked
from mailsim.engine.core import CONTROLLER
from tests.scenarios import ROOT, write_variant

pytestmark = pytest.mark.db

FORBIDDEN = {
    "smtplib", "aiosmtplib", "socket", "ssl", "imaplib", "poplib", "ftplib", "telnetlib",
    "http.client", "urllib.request", "requests", "httpx", "aiohttp", "smtpd", "asyncore",
}  # fmt: skip


@pytest.fixture()
def no_network(monkeypatch):
    """Fail (and record) any socket connection except to the local test database."""
    attempts: list[object] = []
    real_connect = socket.socket.connect

    def guard(self, address, *a, **kw):
        host, port = address[0], address[1] if len(address) > 1 else None
        if port == 54329 and host in ("127.0.0.1", "localhost", "::1"):
            return real_connect(self, address, *a, **kw)
        attempts.append(address)
        raise OSError(f"network blocked in test: {address}")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(
        socket.socket, "connect_ex", lambda self, address: attempts.append(address) or 1
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **k: attempts.append(a) or (_ for _ in ()).throw(OSError("blocked")),
    )
    return attempts


@pytest.mark.parametrize("mode", ["deny", "sink", "event"])
def test_no_socket_is_opened_when_sending_to_external_recipients(env, tmp_path, no_network, mode):
    run = env.run(
        write_variant(
            tmp_path, lambda s: s["environment"]["delivery"].update(external_delivery=mode)
        )
    )
    fm = env.mbx(run, "finance_manager")
    try:
        env.engine.send(
            env.scope,
            run,
            fm,
            to=["vendor@example.test", "x@gmail.com", "y@outlook.com"],
            subject="s",
            text="t",
            actor=CONTROLLER,
        )
    except ExternalDeliveryBlocked:
        assert mode == "deny"
    env.engine.advance(env.scope, run, 700)
    assert env.engine.replay_run(env.scope, run).matches
    assert no_network == []


def test_source_tree_has_no_mail_or_network_imports():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n in FORBIDDEN or n.split(".")[0] in FORBIDDEN:
                    offenders.append(f"{path.relative_to(ROOT)}: {n}")
    assert offenders == []


@pytest.mark.parametrize("module", ["smtplib", "socket", "httpx"])
def test_lint_rule_rejects_forbidden_imports(module):
    code = f"import {module}\n"
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--stdin-filename", "src/mailsim/engine/x.py", "-"],
        input=code, capture_output=True, text=True, cwd=ROOT, check=False,
    )  # fmt: skip
    assert proc.returncode != 0 and "TID251" in proc.stdout


def test_compose_sandbox_network_has_no_egress():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    networks = compose["networks"]
    assert networks["sandbox"]["internal"] is True
    app_networks = compose["services"]["app"]["networks"]
    assert app_networks and all(networks[n].get("internal") is True for n in app_networks)
    assert "ports" not in compose["services"]["app"]
    # only PostgreSQL publishes a port, and only on loopback
    assert compose["services"]["postgres"]["ports"] == ["127.0.0.1:54329:5432"]
    _ = Path
