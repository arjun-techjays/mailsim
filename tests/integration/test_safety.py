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


# The ONLY reviewed exceptions (module -> files). Everything else in FORBIDDEN is banned in
# every file, including the exempt ones (ruff per-file-ignores disable the whole rule).
ALLOWED = {
    "ssl": {"src/mailsim/protocols/tls.py"},
    "subprocess": {"src/mailsim/runner/command.py", "src/mailsim/llm/providers/claude_cli.py"},
    "anthropic": {"src/mailsim/llm/providers/anthropic.py"},
    "httpx": {"src/mailsim/llm/providers/openai_compatible.py"},
}
ALWAYS_FORBIDDEN = FORBIDDEN | {"anthropic", "openai", "aiosmtpd.handlers", "mcp.client", "httpx2"}


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
            rel = str(path.relative_to(ROOT))
            for n in names:
                hit = next(
                    (x for x in sorted(ALWAYS_FORBIDDEN) if n == x or n.startswith(f"{x}.")), None
                )
                if hit and rel not in ALLOWED.get(hit, set()):
                    offenders.append(f"{rel}: {n}")
    assert offenders == []


OUTBOUND_CALLS = {"open_connection", "create_connection", "sock_connect", "open_unix_connection",
                  "create_unix_connection", "create_datagram_endpoint"}  # fmt: skip


def test_source_tree_opens_no_outbound_sockets():
    """asyncio needs no banned import to dial out: forbid the connect calls by name."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
            if name in OUTBOUND_CALLS:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert offenders == []


def test_forbidden_matcher_catches_submodules():
    for n in ("mcp.client.stdio", "aiosmtpd.handlers", "smtplib", "http.client"):
        assert next((x for x in ALWAYS_FORBIDDEN if n == x or n.startswith(f"{x}.")), None), n


def test_exempt_directories_contain_only_listed_files():
    """A new file in an exempted directory is not silently exempt (CI gate)."""
    exempt_dirs = {"src/mailsim/llm/providers", "src/mailsim/protocols", "src/mailsim/runner"}
    allowed_files = set().union(*ALLOWED.values())
    for d in exempt_dirs:
        for path in (ROOT / d).glob("*.py"):
            rel = str(path.relative_to(ROOT))
            if path.name == "__init__.py" or rel in allowed_files:
                continue
            text = path.read_text()
            assert not any(f"import {m}" in text for m in ALWAYS_FORBIDDEN), rel


def test_harness_accessor_is_never_used_by_interfaces():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py")
                 if "harness_parts" in p.read_text() and "mailsim/engine/" not in str(p)]  # fmt: skip
    assert offenders == []


def test_interfaces_never_reach_through_facades():
    """Facades keep the engine in `_e`; nothing outside mailsim/engine may touch it."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if "mailsim/engine/" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr == "_e":
                offenders.append(str(path.relative_to(ROOT)))
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


def test_compose_postgres_recovers_with_syncfs():
    # crash recovery must not fsync every file of every (test) database one by one
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    command = compose["services"]["postgres"]["command"]
    assert command[0] == "postgres" and "recovery_init_sync_method=syncfs" in command
