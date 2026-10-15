"""`claude_cli` provider, exercised with a fake `claude` executable on PATH (no real CLI)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from mailsim.domain.errors import ErrorCode, UpstreamUnavailable
from mailsim.llm.config import LLMConfig
from mailsim.llm.contract import LLMRequest, Message
from mailsim.llm.gateway import LLMGateway, build_provider
from mailsim.llm.providers.claude_cli import ClaudeCliProvider, render_prompt

FAKE = r"""#!{python}
import json, os, sys
argv = sys.argv[1:]
prompt = sys.stdin.read()
record = os.environ.get("FAKE_CLAUDE_RECORD")
if record:
    with open(record, "w") as fh:
        json.dump({{"argv": argv, "stdin": prompt, "cwd": os.getcwd(),
                    "api_key": os.environ.get("ANTHROPIC_API_KEY")}}, fh)
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "ok":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                       "result": "Hello from fake Claude", "total_cost_usd": 0.0012,
                       "usage": {{"input_tokens": 11, "cache_read_input_tokens": 4,
                                  "output_tokens": 7}},
                       "modelUsage": {{"claude-sonnet-5-5": {{}}}}}}))
elif mode == "no_usage":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                       "result": "abcdefgh"}}))
elif mode == "not_logged_in":
    print("Invalid API key · Please run /login", file=sys.stderr); sys.exit(1)
elif mode == "overloaded":
    print(json.dumps({{"type": "result", "is_error": True, "subtype": "error_during_execution",
                       "result": "API Error: 529 overloaded",
                       "usage": {{"input_tokens": 3, "output_tokens": 0}}}})); sys.exit(1)
elif mode == "error_json":
    print(json.dumps({{"type": "result", "is_error": True, "subtype": "error_max_turns",
                       "result": "stopped"}}))
elif mode == "garbage":
    print("not json at all")
elif mode == "no_text":
    print(json.dumps({{"type": "result", "is_error": False, "result": None}}))
elif mode == "crash":
    print("boom", file=sys.stderr); sys.exit(3)
elif mode == "sleep":
    import time; time.sleep(5)
"""


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 12 + "secret")
    return record


def req(*msgs: tuple[str, str], system: str = "", purpose: str = "persona") -> LLMRequest:
    return LLMRequest(purpose=purpose, system=system, run_id="run_1",
                      messages=[Message(role=r, content=c) for r, c in msgs])  # fmt: skip


def test_generates_with_safe_headless_flags_and_login(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    p = ClaudeCliProvider(model="sonnet", timeout_seconds=10)
    hostile = "--dangerously-skip-permissions; rm -rf / $(whoami)"
    out = p.generate(req(("user", hostile), system="-p --model=opus be terse"))
    seen = json.loads(fake_claude.read_text())
    argv = seen["argv"]
    assert argv[:7] == ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                        "--strict-mcp-config"]  # fmt: skip
    assert "--system-prompt=-p --model=opus be terse" in argv and "--model=sonnet" in argv
    assert "--bare" not in argv, "--bare would disable the Claude Code login"
    assert seen["stdin"] == hostile and hostile not in " ".join(argv)
    assert seen["api_key"] is None, "the login is used, never ANTHROPIC_API_KEY"
    assert Path(seen["cwd"]).name.startswith("mailsim-claude-") and not Path(seen["cwd"]).exists()
    assert (out.text, out.provider, out.model) == (
        "Hello from fake Claude",
        "claude_cli",
        "claude-sonnet-5-5",
    )
    assert (out.input_tokens, out.output_tokens, out.usage_estimated) == (15, 7, False)
    assert out.cost_usd == pytest.approx(0.0012)


def test_multi_turn_transcript_and_estimated_usage(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "no_usage")
    p = ClaudeCliProvider()
    request = req(("user", "Q1"), ("assistant", "A1"), ("user", "Q2"))
    out = p.generate(request)
    seen = json.loads(fake_claude.read_text())
    assert "<assistant>" in seen["stdin"] and seen["stdin"] == render_prompt(request)
    assert not any(a.startswith("--model") for a in seen["argv"])
    assert not any(a.startswith("--system-prompt") for a in seen["argv"])
    assert (
        out.usage_estimated is True
        and out.output_tokens == 2
        and out.model == "claude-code-default"
    )


@pytest.mark.parametrize(
    ("mode", "kind", "retryable"),
    [("not_logged_in", "not_logged_in", False), ("overloaded", "unavailable", True),
     ("error_json", "cli_error", False), ("garbage", "bad_response", False),
     ("no_text", "bad_response", False), ("crash", "cli_error", False)],
)  # fmt: skip
def test_failures_map_to_upstream_unavailable(fake_claude, monkeypatch, mode, kind, retryable):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(UpstreamUnavailable) as ei:
        ClaudeCliProvider().generate(req(("user", "hi")))
    assert ei.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert ei.value.details["kind"] == kind and ei.value.details["retryable"] is retryable
    if mode == "not_logged_in":
        assert "/login" in ei.value.message
    if mode == "overloaded":
        assert ei.value.details["input_tokens"] == 3


def test_timeout_is_retryable(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    with pytest.raises(UpstreamUnavailable) as ei:
        ClaudeCliProvider(timeout_seconds=0.5).generate(req(("user", "hi")))
    assert ei.value.details["kind"] == "timeout" and ei.value.details["retryable"] is True


def test_missing_cli_and_os_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(UpstreamUnavailable) as ei:
        ClaudeCliProvider().generate(req(("user", "hi")))
    assert ei.value.details["kind"] == "cli_missing" and "not installed" in ei.value.message
    broken = tmp_path / "claude"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    with pytest.raises(UpstreamUnavailable) as ei:
        ClaudeCliProvider().generate(req(("user", "hi")))
    assert ei.value.details["kind"] == "cli_error"


def test_model_names_are_validated():
    with pytest.raises(ValueError):
        ClaudeCliProvider(model="--dangerously-skip-permissions")
    assert ClaudeCliProvider(model="claude-opus-5-5[1m]").model == "claude-opus-5-5[1m]"


def test_config_gateway_and_recorded_usage(fake_claude, monkeypatch, session_factory, env):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    cfg = LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "claude_cli", "MAILSIM_CLAUDE_CLI": "claude",
                              "PATH": os.environ["PATH"]})  # fmt: skip
    assert cfg.model == "" and cfg.cli_executable == "claude"
    provider = build_provider(cfg)
    assert isinstance(provider, ClaudeCliProvider) and provider.model is None
    gw = LLMGateway(provider, session_factory=session_factory)
    run = env.run()
    result = gw.call(req(("user", "hi"), purpose="judge").model_copy(update={"run_id": run}))
    assert result.response.text == "Hello from fake Claude"
    from mailsim.store import models as m

    with session_factory() as s:
        row = s.get(m.LlmCall, result.llm_call_id)
        assert (row.provider, row.model, row.status) == ("claude_cli", "claude-sonnet-5-5", "ok")
        assert row.usage_estimated is False and row.cost_usd == pytest.approx(0.0012)


@pytest.mark.live_llm
@pytest.mark.skipif(
    os.environ.get("MAILSIM_LIVE_CLAUDE_CLI") != "1" or shutil.which("claude") is None,
    reason="set MAILSIM_LIVE_CLAUDE_CLI=1 with the Claude Code CLI installed and logged in",
)
def test_live_claude_cli_round_trip():
    out = ClaudeCliProvider(timeout_seconds=120).generate(
        req(("user", "Reply with exactly the word: pong"), system="Answer in one word.")
    )
    assert "pong" in out.text.lower() and out.provider == "claude_cli"
