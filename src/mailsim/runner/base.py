"""What every agent runner returns, and the environment contract it gives the agent.

A runner starts the agent under test for one round of one bench trial (`sim bench run`).
The bench passes it the run's agent mailbox facade and the `MAILSIM_*` variables below; the
runner returns an `AgentResult`. Runners never see the scenario spec, the answer key or a
controller/evaluator credential: only what the agent itself may know.

Variables given to the agent (command runner: in its environment; callable runner: in
`AgentContext.env`):

- `MAILSIM_RUN_ID`, `MAILSIM_MAILBOX_ID`, `MAILSIM_MAILBOX_ADDRESS`: the agent's mailbox.
- `MAILSIM_API_KEY`: an agent key bound to that run and mailbox (command runner only).
- `MAILSIM_API_URL`: the `serve` base URL, when the bench was given `--api-url`.
- `MAILSIM_AGENT_BUILD`, `MAILSIM_BENCH_TRIAL`, `MAILSIM_BENCH_ROUND`: bench bookkeeping.
- `MAILSIM_AGENT_REPORT`: optional file the agent may write a JSON object of its own model
  usage to (`model`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`, ...). Only
  scalar values are kept, and the content is recorded as reported (it is not verified).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_REPORT_BYTES = 64 * 1024
MAX_REPORT_KEYS = 32
MAX_REPORT_VALUE_CHARS = 200
TAIL_CHARS = 4000


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    duration_seconds: float
    exit_code: int | None = None
    error: str | None = None
    timed_out: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "duration_seconds": round(self.duration_seconds, 6),
                "exit_code": self.exit_code, "error": self.error, "timed_out": self.timed_out,
                "usage": dict(self.usage), "stdout_tail": self.stdout_tail,
                "stderr_tail": self.stderr_tail}  # fmt: skip


def clean_usage(data: Any) -> dict[str, Any]:
    """The scalar fields of an agent's self-reported usage object (bounded, never nested)."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in sorted(data.items(), key=lambda kv: str(kv[0])):
        if len(out) >= MAX_REPORT_KEYS:
            break
        name = str(key)[:64]
        if isinstance(value, bool | int | float) or value is None:
            out[name] = value
        elif isinstance(value, str):
            out[name] = value[:MAX_REPORT_VALUE_CHARS]
    return out


def read_usage_file(path: Path) -> dict[str, Any]:
    """An agent-written usage report; missing, oversized or malformed files are ignored."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            return {}
        return clean_usage(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def tail(data: bytes | str, secrets: tuple[str, ...] = ()) -> str:
    """The last TAIL_CHARS of captured output, with any secret (the agent key) masked."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[-TAIL_CHARS:]


__all__ = ["AgentResult", "clean_usage", "read_usage_file", "tail"]
