"""`claude_cli` provider: local generation through the Claude Code CLI in headless mode.

Uses the developer's Claude Code login (the CLI's own OAuth/keychain session); the child
process gets an environment WITHOUT ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` so the
login is what is used. Intended for local development only (it spawns a process per call).

Invocation (flags verified against ``claude --help``, Claude Code 2.1.x):

    claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config
           --system-prompt=<system> [--model=<model>]          (prompt on stdin)

- ``-p/--print`` + ``--output-format json``: one JSON result object on stdout.
- ``--tools ""``: no built-in tools, so the CLI only generates text.
- ``--no-session-persistence``: nothing is saved to disk or resumable.
- ``--strict-mcp-config`` (with no ``--mcp-config``): no MCP servers are loaded.
- ``--system-prompt=``: replaces the default system prompt. ``--bare`` is deliberately NOT
  used: it disables OAuth/keychain auth and would force an API key.
- The process runs in an empty temporary working directory so no project ``CLAUDE.md`` is
  picked up; user-level memory may still apply (documented limitation).

The prompt goes through stdin and every option value is passed with ``--flag=value`` in an
argument list (no shell), so prompt or system text can never become CLI options.
Usage: ``usage.input_tokens``/``output_tokens`` (+ cache tokens) and ``total_cost_usd`` from
the JSON result are used when present; otherwise tokens are estimated (chars/4) and the
response is marked ``usage_estimated``. Replay never calls this provider: outputs are
recorded by the persona runtime / judge like any other provider.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from typing import Any

from mailsim.llm.contract import LLMRequest, LLMResponse, ProviderName

DEFAULT_EXECUTABLE = "claude"
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
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

    def command(self, request: LLMRequest, exe: str) -> list[str]:
        args = [exe, "-p", "--output-format", "json", "--tools", "",
                "--no-session-persistence", "--strict-mcp-config"]  # fmt: skip
        if request.system:
            args.append(f"--system-prompt={request.system}")
        if self.model:
            args.append(f"--model={self.model}")
        return args

    def _env(self) -> dict[str, str]:
        base = dict(os.environ if self._environ is None else self._environ)
        for key in STRIPPED_ENV:
            base.pop(key, None)
        return base

    def generate(self, request: LLMRequest) -> LLMResponse:
        from mailsim.llm.gateway import provider_error  # local: gateway imports providers

        env = self._env()
        exe = shutil.which(self.executable, path=env.get("PATH"))
        if exe is None:
            raise provider_error("claude_cli", "cli_missing",
                                 f"the Claude Code CLI ({self.executable!r}) is not installed "
                                 "or not on PATH", retryable=False)  # fmt: skip
        prompt = render_prompt(request)
        try:
            with tempfile.TemporaryDirectory(prefix="mailsim-claude-") as cwd:
                proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, prompt via stdin
                    self.command(request, exe), input=prompt, capture_output=True, text=True,
                    timeout=self.timeout_seconds, cwd=cwd, env=env, check=False,
                )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise provider_error("claude_cli", "timeout",
                                 f"no answer within {self.timeout_seconds:g}s",
                                 retryable=True) from None  # fmt: skip
        except OSError as exc:
            raise provider_error("claude_cli", "cli_error", type(exc).__name__,
                                 retryable=False) from None  # fmt: skip
        data = self._parse(proc.stdout)
        if proc.returncode != 0 or data is None or data.get("is_error"):
            raise self._failure(proc, data, prompt)
        return self._response(data, prompt)

    @staticmethod
    def _parse(stdout: str) -> dict[str, Any] | None:
        try:
            value = json.loads(stdout.strip() or "null")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _failure(self, proc: subprocess.CompletedProcess[str], data: dict[str, Any] | None,
                 prompt: str) -> Exception:  # fmt: skip
        from mailsim.llm.gateway import provider_error

        detail = (
            " ".join(
                str(x)
                for x in ((data or {}).get("result"), (data or {}).get("subtype"), proc.stderr)
                if x  # fmt: skip
            ).strip()[:300]
            or f"exit status {proc.returncode}"
        )
        low = detail.lower()
        usage = self._usage(data, prompt, "")[:2] if data else None
        if any(h in low for h in _LOGIN_HINTS):
            return provider_error("claude_cli", "not_logged_in",
                                  "the Claude Code CLI is not logged in (run `claude` and "
                                  f"`/login`): {detail}", retryable=False, usage=usage)  # fmt: skip
        if any(h in low for h in _TRANSIENT_HINTS):
            return provider_error("claude_cli", "unavailable", detail, retryable=True, usage=usage)
        if data is None:
            return provider_error("claude_cli", "bad_response" if proc.returncode == 0 else "cli_error",
                                  detail, retryable=False)  # fmt: skip
        return provider_error("claude_cli", "cli_error", detail, retryable=False, usage=usage)

    def _usage(self, data: dict[str, Any] | None, prompt: str, text: str) -> tuple[int, int, bool]:
        usage = (data or {}).get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
            tokens_in = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))  # fmt: skip
            return tokens_in, int(usage["output_tokens"]), False
        return _estimate(prompt), _estimate(text), True

    def _response(self, data: dict[str, Any], prompt: str) -> LLMResponse:
        from mailsim.llm.gateway import provider_error

        text = data.get("result")
        if not isinstance(text, str):
            raise provider_error("claude_cli", "bad_response", "JSON result has no text",
                                 retryable=False)  # fmt: skip
        tokens_in, tokens_out, estimated = self._usage(data, prompt, text)
        model = self.model or "claude-code-default"
        model_usage = data.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model = str(next(iter(model_usage)))
        cost = data.get("total_cost_usd")
        return LLMResponse(
            text=text,
            provider="claude_cli",
            model=model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            stop_reason=str(data.get("stop_reason") or data.get("subtype") or "end_turn"),
            usage_estimated=estimated,
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )
