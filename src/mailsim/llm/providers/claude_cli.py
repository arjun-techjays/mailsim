"""`claude_cli` provider: local generation through the Claude Code CLI in headless mode.

Uses the developer's Claude Code login (the CLI's own OAuth/keychain session). The child
process gets an ALLOWLISTED environment (PATH, HOME, locale, temp and config dirs): no
``ANTHROPIC_*`` variables (so the login is what is used and no base-URL override can redirect
it), no database URL and no other provider secrets. Intended for local development only (it
spawns a process per call).

Invocation (flags verified against ``claude --help``, Claude Code 2.1.x):

    claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config
           --system-prompt-file=<tmp file> [--model=<model>]          (prompt on stdin)

- ``-p/--print`` + ``--output-format json``: one JSON result object on stdout.
- ``--tools ""``: no built-in tools, so the CLI only generates text.
- ``--no-session-persistence``: nothing is saved to disk or resumable.
- ``--strict-mcp-config`` (with no ``--mcp-config``): no MCP servers are loaded.
- ``--system-prompt-file=``: replaces the default system prompt; a file (in the private temp
  working directory) so large prompts never hit the per-argument size limit. ``--bare`` is
  deliberately NOT used: it disables OAuth/keychain auth and would force an API key.
- ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` = the request's ``max_tokens`` (the gateway caps it to the
  remaining budget). ``MAILSIM_LLM_EFFORT`` does not apply to this provider.
- The process runs in an empty temporary working directory so no project ``CLAUDE.md`` is
  picked up; user-level Claude Code settings/memory may still apply (documented limitation).
- It runs in its own process group; on timeout the whole group is killed.

The prompt goes through stdin and every option value is passed with ``--flag=value`` in an
argument list (no shell), so prompt or system text can never become CLI options.
Usage: ``usage.input_tokens``/``output_tokens`` (+ cache tokens) and ``total_cost_usd`` from
the JSON result are used when present (on failures too); otherwise tokens are estimated
(chars/4) and marked ``usage_estimated``. Error messages never echo the CLI's ``result`` text
(it can be model output derived from email content). Replay never calls this provider:
outputs are recorded by the persona runtime / judge like any other provider.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mailsim.llm.contract import LLMRequest, LLMResponse, ProviderName

DEFAULT_EXECUTABLE = "claude"
ENV_ALLOWLIST = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR", "TERM",
                           "CLAUDE_CONFIG_DIR", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS"})  # fmt: skip
ENV_PREFIXES = ("LC_", "XDG_")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-\[\]]{0,99}$")
_LOGIN_HINTS = ("not logged in", "please run /login", "login required", "authentication",
                "unauthorized", "invalid api key", "oauth")  # fmt: skip
_TRANSIENT_HINTS = ("overloaded", "rate limit", "rate_limit", "429", "529", "timeout",
                    "temporarily", "econnreset", "network")  # fmt: skip


def _estimate(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0


def render_prompt(request: LLMRequest) -> str:
    """Single user turn -> its content; a multi-turn conversation -> a labelled transcript."""
    if len(request.messages) == 1 and request.messages[0].role == "user":
        return request.messages[0].content
    lines = ["Conversation so far (respond as the assistant to the final user message):", ""]
    for msg in request.messages:
        lines += [f"<{msg.role}>", msg.content, f"</{msg.role}>", ""]
    return "\n".join(lines)


class _Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ClaudeCliProvider:
    name: ProviderName = "claude_cli"

    def __init__(self, *, model: str | None = None, timeout_seconds: float = 120.0,
                 executable: str = DEFAULT_EXECUTABLE,
                 environ: Mapping[str, str] | None = None) -> None:  # fmt: skip
        if model is not None and not _MODEL_RE.fullmatch(model):
            raise ValueError("invalid model name for the Claude Code CLI")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.executable = executable
        self._environ = environ

    def command(self, exe: str, system_file: Path | None) -> list[str]:
        args = [exe, "-p", "--output-format", "json", "--tools", "",
                "--no-session-persistence", "--strict-mcp-config"]  # fmt: skip
        if system_file is not None:
            args.append(f"--system-prompt-file={system_file}")
        if self.model:
            args.append(f"--model={self.model}")
        return args

    def _env(self, request: LLMRequest) -> dict[str, str]:
        base = os.environ if self._environ is None else self._environ
        env = {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.startswith(ENV_PREFIXES)}
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(request.max_tokens)
        return env

    def generate(self, request: LLMRequest) -> LLMResponse:
        from mailsim.llm.gateway import provider_error  # local: gateway imports providers

        env = self._env(request)
        exe = shutil.which(self.executable, path=env.get("PATH"))
        if exe is None:
            raise provider_error("claude_cli", "cli_missing",
                                 f"the Claude Code CLI ({self.executable!r}) is not installed "
                                 "or not on PATH", retryable=False)  # fmt: skip
        prompt = render_prompt(request)
        try:
            with tempfile.TemporaryDirectory(prefix="mailsim-claude-") as cwd:
                system_file = None
                if request.system:
                    system_file = Path(cwd) / "system-prompt.txt"
                    system_file.write_text(request.system, encoding="utf-8")
                proc = self._run(self.command(exe, system_file), prompt, cwd, env)
        except OSError as exc:
            raise provider_error("claude_cli", "cli_error", type(exc).__name__,
                                 retryable=False) from None  # fmt: skip
        data = self._parse(proc.stdout)
        if proc.returncode != 0 or data is None or data.get("is_error"):
            raise self._failure(proc, data, prompt)
        return self._response(data, prompt)

    def _run(self, argv: list[str], prompt: str, cwd: str, env: dict[str, str]) -> _Result:
        from mailsim.llm.gateway import provider_error

        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, prompt via stdin
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=env, start_new_session=True,
        )  # fmt: skip
        try:
            out, err = proc.communicate(prompt, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)  # the CLI and anything it spawned
            proc.communicate()
            raise provider_error("claude_cli", "timeout",
                                 f"no answer within {self.timeout_seconds:g}s",
                                 retryable=True) from None  # fmt: skip
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # nothing the CLI spawned outlives the call
        return _Result(proc.returncode, out, err)

    @staticmethod
    def _parse(stdout: str) -> dict[str, Any] | None:
        try:
            value = json.loads(stdout.strip() or "null")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _cost(data: dict[str, Any] | None) -> float | None:
        cost = (data or {}).get("total_cost_usd")
        return float(cost) if isinstance(cost, int | float) else None

    def _failure(self, proc: _Result, data: dict[str, Any] | None, prompt: str) -> Exception:
        from mailsim.llm.gateway import provider_error

        # classify on everything, but never echo the CLI's `result` text (it can be model output
        # derived from email content) into errors / llm_calls: only the subtype and stderr
        result = str((data or {}).get("result") or "")
        detail = (
            " ".join(str(x) for x in ((data or {}).get("subtype"), proc.stderr) if x).strip()[:300]
            or f"exit status {proc.returncode}"
        )
        low = f"{detail} {result}".lower()
        billed: dict[str, Any] = {}
        if data is not None:
            tokens_in, tokens_out, estimated = self._usage(data, prompt, "")
            billed = {"usage": (tokens_in, tokens_out), "usage_estimated": estimated,
                      "cost_usd": self._cost(data)}  # fmt: skip
        if any(h in low for h in _LOGIN_HINTS):
            return provider_error("claude_cli", "not_logged_in",
                                  "the Claude Code CLI is not logged in (run `claude` and "
                                  f"`/login`): {detail}", retryable=False, **billed)  # fmt: skip
        if any(h in low for h in _TRANSIENT_HINTS):
            return provider_error("claude_cli", "unavailable", detail, retryable=True, **billed)
        if data is None:
            return provider_error("claude_cli", "bad_response" if proc.returncode == 0 else "cli_error",
                                  detail, retryable=False)  # fmt: skip
        return provider_error("claude_cli", "cli_error", detail, retryable=False, **billed)

    def _usage(self, data: dict[str, Any] | None, prompt: str, text: str) -> tuple[int, int, bool]:
        usage = (data or {}).get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
            tokens_in = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))  # fmt: skip
            return tokens_in, int(usage["output_tokens"]), False
        return _estimate(prompt), _estimate(text), True

    def _model(self, data: dict[str, Any]) -> str:
        """The main model: the `modelUsage` entry with the most output tokens."""
        model_usage = data.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:

            def out_tokens(item: tuple[str, Any]) -> int:
                v = item[1].get("outputTokens") if isinstance(item[1], dict) else None
                return v if isinstance(v, int) else -1

            return str(max(model_usage.items(), key=out_tokens)[0])
        return self.model or "claude-code-default"

    def _response(self, data: dict[str, Any], prompt: str) -> LLMResponse:
        from mailsim.llm.gateway import provider_error

        text = data.get("result")
        if not isinstance(text, str):
            raise provider_error("claude_cli", "bad_response", "JSON result has no text",
                                 retryable=False)  # fmt: skip
        tokens_in, tokens_out, estimated = self._usage(data, prompt, text)
        return LLMResponse(
            text=text,
            provider="claude_cli",
            model=self._model(data),
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            stop_reason=str(data.get("stop_reason") or data.get("subtype") or "end_turn"),
            usage_estimated=estimated,
            cost_usd=self._cost(data),
        )
