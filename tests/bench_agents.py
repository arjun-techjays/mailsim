"""Tiny deterministic agents for the `sim bench` tests (finance.invoice-bank-change)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

CALLS: list[dict[str, Any]] = []


def _invoice(mailbox: Any) -> Any:
    return next(m for m in mailbox.list_messages(folder="inbox").items
                if "INV-001" in m.subject and m.from_.email == "vendor@example.test")  # fmt: skip


def good(ctx: Any) -> dict[str, Any]:
    """Replies to the vendor and asks for independent verification: passes the key."""
    CALLS.append({"run_id": ctx.run_id, "trial": ctx.trial, "round": ctx.round,
                  "address": ctx.mailbox_address})  # fmt: skip
    if ctx.round == 1:
        ctx.mailbox.reply(_invoice(ctx.mailbox).id,
                          text="Thanks. We will verify the bank details independently first.")  # fmt: skip
    return {"model": "scripted-good", "input_tokens": 3, "output_tokens": 2, "nested": {"x": 1}}


def silent(ctx: Any) -> None:
    """Does nothing: fails the `replied-to-vendor` assertion."""


def flaky(ctx: Any) -> None:
    """Passes on odd trials only (pass rate 50% over 2 trials)."""
    if ctx.trial % 2:
        good(ctx)


def broken(ctx: Any) -> None:
    raise RuntimeError("agent crashed")


not_callable = 42


def main() -> int:  # `python -m tests.bench_agents <mode>`: a command agent (no network)
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    print("key=" + os.environ.get("MAILSIM_API_KEY", ""))
    print("db=" + os.environ.get("MAILSIM_DATABASE_URL", "<unset>"), file=sys.stderr)
    with open(os.environ["MAILSIM_AGENT_REPORT"], "w", encoding="utf-8") as fh:
        json.dump({"model": "cmd-model", "input_tokens": 7, "list": [1]}, fh)
    if mode == "sleep":
        import time

        time.sleep(30)
    return 3 if mode == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
