"""Callable runner: run an in-process Python agent, `package.module:function`.

The function is called once per bench round with an `AgentContext` and drives the run's agent
mailbox facade directly (the actor is fixed to `agent`; actions are recorded with the
interface `api`). It may return a dict of its own model usage (same fields as the command
runner's `MAILSIM_AGENT_REPORT`). An exception is an agent error. There is no timeout: an
in-process call cannot be interrupted safely, so use the command runner for untrusted agents.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mailsim.engine.context import use_interface
from mailsim.engine.facades import AgentMailbox
from mailsim.runner.base import AgentResult, clean_usage


@dataclass(frozen=True)
class AgentContext:
    mailbox: AgentMailbox
    env: dict[str, str]

    @property
    def run_id(self) -> str:
        return self.env["MAILSIM_RUN_ID"]

    @property
    def mailbox_address(self) -> str:
        return self.env["MAILSIM_MAILBOX_ADDRESS"]

    @property
    def trial(self) -> int:
        return int(self.env["MAILSIM_BENCH_TRIAL"])

    @property
    def round(self) -> int:
        return int(self.env["MAILSIM_BENCH_ROUND"])


AgentFunction = Callable[[AgentContext], Any]


def load_callable(target: str) -> AgentFunction:
    module, sep, name = target.partition(":")
    if not sep or not module or not name:
        raise ValueError(f"agent callable must be 'package.module:function', not {target!r}")
    obj: Any = importlib.import_module(module)
    for part in name.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise ValueError(f"{target} is not callable")
    return obj  # type: ignore[no-any-return]


class CallableRunner:
    needs_api_key = False

    def __init__(self, target: str, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.target = target
        self.fn = load_callable(target)
        self._clock = clock

    def describe(self) -> dict[str, Any]:
        return {"type": "callable", "callable": self.target}

    def run(self, *, mailbox: Any, env: Mapping[str, str], timeout: float) -> AgentResult:
        del timeout  # see the module docstring
        started = self._clock()
        try:
            with use_interface("api"):
                returned = self.fn(AgentContext(mailbox=mailbox, env=dict(env)))
        except Exception as exc:  # noqa: BLE001 - any agent failure is a recorded agent error
            return AgentResult(ok=False, duration_seconds=self._clock() - started,
                               error=f"{type(exc).__name__}: {str(exc)[:500]}")  # fmt: skip
        return AgentResult(ok=True, duration_seconds=self._clock() - started,
                           usage=clean_usage(returned))  # fmt: skip


__all__ = ["AgentContext", "AgentFunction", "CallableRunner", "load_callable"]
