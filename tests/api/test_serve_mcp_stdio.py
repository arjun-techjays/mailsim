"""Integrator wiring: `python -m mailsim.serve mcp-stdio`."""

from __future__ import annotations

import pytest

from mailsim import serve as server
from mailsim.mcp.stdio import StdioConfigError

pytestmark = pytest.mark.db


def _env(db_engine, tmp_path, **extra: str) -> dict[str, str]:
    url = db_engine.url.render_as_string(hide_password=False)
    return {"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path), **extra}


def test_mcp_stdio_wires_state_and_never_local_no_auth(db_engine, tmp_path, capsys):
    seen = {}

    def run(engine, **kw):
        seen.update(kw, engine=engine)

    env = _env(db_engine, tmp_path, MAILSIM_API_KEY="msk_x", MAILSIM_LOCAL_NO_AUTH="1")
    assert server.mcp_stdio_command(env, run=run) == 0
    assert seen["environ"]["MAILSIM_API_KEY"] == "msk_x"
    assert "MAILSIM_LOCAL_NO_AUTH" not in seen["environ"]
    assert {"session_factory", "evaluation_service", "persona_runtime_factory"} <= set(seen)
    out = capsys.readouterr()
    assert out.out == "" and "LOCAL NO-AUTH" not in out.err  # stdout is the protocol channel


def test_mcp_stdio_config_errors_exit_2(db_engine, tmp_path, capsys):
    def run(engine, **kw):
        raise StdioConfigError("set MAILSIM_API_KEY")

    assert server.mcp_stdio_command(_env(db_engine, tmp_path), run=run) == 2
    out = capsys.readouterr()
    assert out.out == "" and "mcp-stdio: set MAILSIM_API_KEY" in out.err


def test_mcp_stdio_subcommand_is_routed(monkeypatch):
    monkeypatch.setattr(server, "mcp_stdio_command", lambda: 7)
    assert server.main(["mcp-stdio"]) == 7
