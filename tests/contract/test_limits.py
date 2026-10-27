"""Per-run resource caps (spec 10, issue #7), table-driven across every agent interface.

Each row of CAPS sets one small cap on a fresh run and exhausts it through the driver's
native calls. Every driver must then refuse with its own quota error (REST/Gmail/Graph 429,
MCP tool error, IMAP `NO [LIMIT]`, SMTP 452), tell the agent nothing about the simulation or
the cap, and the refusal must appear in the run audit as a denied agent action naming the cap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from tests.contract.drivers import (
    DRIVERS,
    ImapSmtpDriver,
    agent_error_text,
    imap_append_sent,
    quota_refusal,
)
from tests.scenarios import write_variant

pytestmark = pytest.mark.db

MAX_CALLS = 200  # a driver call that never hits its cap fails the test, never loops forever
_SENT_COPY = (b"From: finance.manager@acme.test\r\nTo: vendor@example.test\r\n"
              b"Subject: filed copy\r\nMessage-ID: <copy@acme.test>\r\n\r\nbody\r\n")  # fmt: skip


@dataclass(frozen=True)
class Cap:
    name: str  # the scenario `evaluation.limits` key, and `details.cap` of the refusal
    value: int
    operations: frozenset[str]  # engine operations the refusal may be recorded under
    native: str  # which native refusal the IMAP+SMTP driver shows ("imap" or "smtp")
    call: Callable[[Any, Any, int], None]  # (driver, first inbox ref, attempt number)
    needs_ref: bool = True
    only: type | None = None  # the one driver that can express the operation


CAPS = [
    # the existing write budget, through each interface's send (SMTP for imap_smtp)
    Cap("max_agent_actions", 2, frozenset({"send"}), "smtp",
        lambda d, ref, i: d.send(["vendor@example.test"], f"cap {i}", "x"), needs_ref=False),
    Cap("max_agent_reads", 6, frozenset({"read", "search"}), "imap",
        lambda d, ref, i: d.list("inbox"), needs_ref=False),
    # each new label is a label event plus the counted action: two agent events per call
    Cap("max_agent_events", 6, frozenset({"label", "read", "search"}), "imap",
        lambda d, ref, i: d.label(ref, [f"ev{i}"])),
    Cap("max_labels_per_message", 2, frozenset({"label"}), "imap",
        lambda d, ref, i: d.label(ref, [f"kw{i}"])),
    Cap("max_sent_copies", 1, frozenset({"sent_copy"}), "imap",
        lambda d, ref, i: imap_append_sent(d, _SENT_COPY), needs_ref=False,
        only=ImapSmtpDriver),
]  # fmt: skip


class CappedWorld:
    def __init__(self, env: Any, tmp_path: Any, cap: Cap) -> None:
        def tweak(s: dict[str, Any]) -> None:
            s["agent"]["allowed_operations"] = []
            s["agent"]["prohibited_operations"] = ["external_delivery"]
            limits = s["evaluation"]["limits"]
            limits["max_agent_actions"] = 500
            limits[cap.name] = cap.value

        self.env = env
        ctl = Controller(env.engine, env.scope)
        self.ev = EvaluatorView(env.engine, env.scope)
        self.run = ctl.create_run(env.version(write_variant(tmp_path, tweak)))
        ctl.start_run(self.run)
        self.mailbox = ctl.mailbox_id_for(self.run, "finance_manager")

    def driver(self, cls: type) -> Any:
        return cls(AgentMailbox(self.env.engine, self.env.scope, self.run, self.mailbox))

    def refusals(self, cap: str) -> list[dict[str, Any]]:
        return [a for a in self.ev.actions(self.run)
                if a["actor"] == "agent" and a["outcome"] == "SIMULATION_LIMIT_EXCEEDED"
                and (a["result"].get("details") or {}).get("cap") == cap]  # fmt: skip


def _exhaust(d: Any, cap: Cap, ref: Any) -> BaseException:
    for i in range(MAX_CALLS):
        try:
            cap.call(d, ref, i)
        except Exception as exc:  # noqa: BLE001 - the refusal is what this test inspects
            return exc
    pytest.fail(f"{cap.name}={cap.value} never refused after {MAX_CALLS} calls")


@pytest.mark.parametrize("cap", CAPS, ids=lambda c: c.name)
@pytest.mark.parametrize("drv", DRIVERS, ids=lambda d: d.name)
def test_cap_is_refused_natively_and_audited(env, tmp_path, drv, cap):
    if cap.only is not None and drv is not cap.only:
        pytest.skip(f"{drv.name} has no operation that files a sent copy (IMAP APPEND only)")
    world = CappedWorld(env, tmp_path, cap)
    d = world.driver(drv)
    ref = d.list("inbox")[0].ref if cap.needs_ref else None
    exc = _exhaust(d, cap, ref)

    # the interface's own quota refusal
    expected = {"neutral": "neutral", "rest": "rest", "gmail": "gmail", "graph": "graph",
                "mcp": "mcp", "imap_smtp": cap.native}[drv.name]  # fmt: skip
    assert quota_refusal(exc) == expected, f"{drv.name}: {exc!r}"
    if drv.name != "neutral":  # the facade is the operators' view; interfaces are the agent's
        told = agent_error_text(exc).lower()
        assert cap.name not in told and "simulation" not in told and "limit of" not in told

    # the run audit: a denied agent action naming the cap, under the expected operation
    refused = world.refusals(cap.name)
    assert refused, f"no audited refusal of {cap.name}"
    assert {a["operation"] for a in refused} <= cap.operations
    assert refused[0]["result"]["details"] == {"limit": cap.value, "cap": cap.name}
    assert refused[0]["result"]["code"] == "SIMULATION_LIMIT_EXCEEDED"


@pytest.mark.parametrize("drv", DRIVERS, ids=lambda d: d.name)
def test_label_cap_leaves_the_message_unchanged(env, tmp_path, drv):
    cap = next(c for c in CAPS if c.name == "max_labels_per_message")
    world = CappedWorld(env, tmp_path, cap)
    d = world.driver(drv)
    ref = d.list("inbox")[0].ref
    _exhaust(d, cap, ref)
    canonical = d.get(ref).canonical_id

    def labels() -> set[str]:
        msg = next(m for m in world.ev.state(world.run)["messages"] if m["id"] == canonical)
        return set(msg["labels"])

    assert labels() == {"kw0", "kw1"}  # the refused label was not applied
    # removing is never capped: the agent can still tidy up (here through the neutral facade)
    world.driver(DRIVERS[0]).mb.modify_labels(canonical, remove=["kw0"])
    assert labels() == {"kw1"}
