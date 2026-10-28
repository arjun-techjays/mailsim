"""Command runner: start the agent under test as a subprocess (the narrow, reviewed exemption
from the process ban; see pyproject's per-file ignores and tests/integration/test_safety.py).

The command is split with `shlex` and run WITHOUT a shell, in its own process group (killed
as a whole on timeout). Its environment is minimal on purpose: a few basics (`PATH`, `HOME`,
locale, temp dir), the variables named with `--pass-env`, and the `MAILSIM_*` agent variables
(runner/base.py). Operator secrets such as `MAILSIM_DATABASE_URL` or controller/evaluator keys
are NOT inherited unless passed explicitly: with the database URL an agent could read its
own answer key. Output is captured (never mixed into the bench's JSON) and only its tail is
kept, with the agent key masked.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from mailsim.runner.base import AgentResult, read_usage_file, tail

BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "PYTHONPATH")


class CommandRunner:
    """Run `command` once per bench round; exit code 0 = the agent finished normally."""

    needs_api_key = True

    def __init__(self, command: str | Sequence[str], *, pass_env: Sequence[str] = (),
                 cwd: Path | None = None, environ: Mapping[str, str] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.argv:
            raise ValueError("agent command is empty")
        self.pass_env = tuple(pass_env)
        self.cwd = cwd
        self._environ = os.environ if environ is None else environ
        self._clock = clock

    def describe(self) -> dict[str, Any]:
        joined = shlex.join(self.argv)
        return {"type": "command", "command": joined,
                "command_sha256": hashlib.sha256(joined.encode()).hexdigest(),
                "pass_env": sorted(self.pass_env)}  # fmt: skip

    def environment(self, agent_env: Mapping[str, str]) -> dict[str, str]:
        names = (*BASE_ENV, *self.pass_env)
        env = {n: self._environ[n] for n in names if n in self._environ}
        env.update(agent_env)
        return env

    def run(self, *, mailbox: Any, env: Mapping[str, str], timeout: float) -> AgentResult:
        del mailbox  # a subprocess reaches the mailbox through an interface, with its key
        with tempfile.TemporaryDirectory(prefix="mailsim-agent-") as tmp:
            report = Path(tmp) / "agent-report.json"
            out_path, err_path = Path(tmp) / "stdout", Path(tmp) / "stderr"
            full_env = self.environment({**env, "MAILSIM_AGENT_REPORT": str(report)})
            secrets = (env.get("MAILSIM_API_KEY", ""),)
            started = self._clock()
            timed_out, code, error = False, None, None
            with out_path.open("wb") as out, err_path.open("wb") as err:
                try:
                    proc = subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=err, env=full_env, cwd=self.cwd,
                                            start_new_session=True)  # fmt: skip
                except OSError as exc:
                    error = f"could not start the agent: {exc.strerror or exc}"
                else:
                    try:
                        code = proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _kill_group(proc)
                        code = proc.wait()
                        error = f"agent timed out after {timeout:g}s"
            duration = self._clock() - started
            if error is None and code != 0:
                error = f"agent exited with status {code}"
            return AgentResult(
                ok=error is None, duration_seconds=duration, exit_code=code,
                error=error, timed_out=timed_out, usage=read_usage_file(report),
                stdout_tail=tail(out_path.read_bytes(), secrets),
                stderr_tail=tail(err_path.read_bytes(), secrets),
            )  # fmt: skip


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the agent and everything it started (its own session / process group)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except ProcessLookupError:  # pragma: no cover - already gone
        pass


__all__ = ["BASE_ENV", "CommandRunner"]
