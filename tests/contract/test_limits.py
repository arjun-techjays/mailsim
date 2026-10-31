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


# ---- a write is refused before it happens, or it succeeds: never "quota exceeded" after a
# committed write (the interface's read-back of its own write is exempt from the read/event caps)
WRITE_CAPS = [("max_agent_reads", 4), ("max_agent_events", 6), ("max_agent_actions", 3)]


def _mailbox_state(world: CappedWorld) -> list[tuple[Any, ...]]:
    return sorted((m["id"], m["folder_id"], tuple(m["labels"]), tuple(m["flags"]))
                  for m in world.ev.state(world.run)["messages"]
                  if m["mailbox_id"] == world.mailbox)  # fmt: skip


@pytest.mark.parametrize(("cap", "value"), WRITE_CAPS, ids=[c for c, _ in WRITE_CAPS])
@pytest.mark.parametrize("drv", DRIVERS, ids=lambda d: d.name)
def test_label_writes_after_a_cap_are_refused_cleanly_or_succeed(env, tmp_path, drv, cap, value):
    world = CappedWorld(env, tmp_path, Cap(cap, value, frozenset(), "imap", lambda *_: None))
    d = world.driver(drv)
    ref = canonical = None
    for i in range(40):
        before = _mailbox_state(world)
        try:
            if ref is None:
                first = d.list("inbox")[0]
                ref, canonical = first.ref, first.canonical_id
            d.label(ref, [f"w{i}"])
        except Exception as exc:  # noqa: BLE001 - the refusal is what this test inspects
            assert quota_refusal(exc) is not None, f"{drv.name}: {exc!r}"
            assert _mailbox_state(world) == before, "refused, yet the mailbox changed"
            return
        labels = next(m["labels"] for m in world.ev.state(world.run)["messages"]
                      if m["id"] == canonical)  # fmt: skip
        assert f"w{i}" in labels, "succeeded, yet the label is missing"
    if cap != "max_agent_reads":  # a write that needs no reads of its own may never be refused
        pytest.fail(f"{cap}={value} never refused a write")


@pytest.mark.parametrize(("cap", "value"), WRITE_CAPS, ids=[c for c, _ in WRITE_CAPS])
@pytest.mark.parametrize("kind", ["send", "insert", "modify"])
def test_gmail_writes_after_a_cap_are_refused_cleanly_or_succeed(env, tmp_path, kind, cap, value):
    """Gmail answers a send/insert/modify with the stored message: that read-back may not turn a
    committed write into a 429 (a retrying client would repeat it)."""
    from tests.contract.drivers import GmailDriver

    world = CappedWorld(env, tmp_path, Cap(cap, value, frozenset(), "imap", lambda *_: None))
    d = world.driver(GmailDriver)
    base = "/gmail/v1/users/me"
    gid = None
    if kind == "modify":
        gid = d.http.get(base + "/messages", params={"labelIds": "INBOX"}).json()["messages"][0][
            "id"
        ]
    refused = 0
    for i in range(30):
        before = _mailbox_state(world)
        raw = d._raw({"From": "finance@example.test", "To": "vendor@example.test",
                      "Subject": f"{kind} {i}"}, "x")  # fmt: skip
        if kind == "send":
            r = d.http.post(base + "/messages/send", json={"raw": raw})
        elif kind == "insert":
            r = d.http.post(base + "/messages", json={"raw": raw, "labelIds": ["INBOX"]})
        else:  # toggle the star from the current state, so every call is a real change
            starred = any("FLAGGED" in flags for _id, _f, _l, flags in before)
            change = "removeLabelIds" if starred else "addLabelIds"
            r = d.http.post(f"{base}/messages/{gid}/modify", json={change: ["STARRED"]})
        after = _mailbox_state(world)
        if r.status_code == 200:
            assert after != before, f"{kind} answered 200 but changed nothing"
            continue
        assert r.status_code == 429, r.text
        assert after == before, f"{kind} answered 429 after a committed write"
        refused += 1
        if refused == 2:
            return
    if cap != "max_agent_reads":  # writes need no reads of their own: they may never refuse
        pytest.fail(f"{cap}={value} never refused a Gmail {kind}")


def test_gmail_insert_over_the_label_cap_stores_nothing(env, tmp_path):
    from tests.contract.drivers import GmailDriver

    world = CappedWorld(env, tmp_path, Cap("max_labels_per_message", 2, frozenset(), "imap",
                                           lambda *_: None))  # fmt: skip
    d = world.driver(GmailDriver)
    base = "/gmail/v1/users/me"
    ids = [d.http.post(base + "/labels", json={"name": n}).json()["id"] for n in ("a", "b", "c")]
    raw = d._raw({"From": "finance@example.test", "To": "vendor@example.test", "Subject": "i"}, "x")
    before = _mailbox_state(world)
    r = d.http.post(base + "/messages", json={"raw": raw, "labelIds": ["INBOX", *ids]})
    assert r.status_code == 429 and r.json()["error"]["details"][0]["reason"] == "QUOTA_EXCEEDED"
    assert _mailbox_state(world) == before
    assert world.refusals("max_labels_per_message")[-1]["operation"] == "insert"
    r = d.http.post(base + "/messages", json={"raw": raw, "labelIds": ["INBOX", *ids[:2]]})
    assert r.status_code == 200 and len(_mailbox_state(world)) == len(before) + 1


def test_gmail_batch_items_count_their_own_reads(env, tmp_path):
    """One item's write never exempts another item's reads: a batch that repeats an id after
    a single real write still counts, and is capped by, its reads (one per item, issue #24)."""
    from tests.contract.drivers import GmailDriver

    world = CappedWorld(env, tmp_path, Cap("max_agent_reads", 4, frozenset(), "imap",
                                           lambda *_: None))  # fmt: skip
    d = world.driver(GmailDriver)
    base = "/gmail/v1/users/me"
    gid = d.http.get(base + "/messages").json()["messages"][0]["id"]  # read 1
    r = d.http.post(base + "/messages/batchModify",
                    json={"ids": [gid] * 1000, "addLabelIds": ["STARRED"]})  # fmt: skip
    assert r.status_code == 429  # the second item's reads hit the cap
    reads = [a for a in world.ev.actions(world.run) if a["operation"] == "read"]
    assert sum(a["outcome"] == "ok" for a in reads) <= 12  # list + 3 items, not ~3000
    assert world.refusals("max_agent_reads")
    flagged = [m for m in world.ev.state(world.run)["messages"] if "FLAGGED" in m["flags"]]
    assert len(flagged) == 1  # the first item's write stands
    with world.env.session_factory() as s:
        from mailsim.store import models as m

        assert s.get(m.Run, world.run).agent_reads == 4


@pytest.mark.parametrize("path", ["batchModify", "batchDelete"])
def test_gmail_batch_of_one_answers_after_its_write(env, tmp_path, path):
    """Within one item, the read-back after its own write stays exempt (a batch at the read
    cap whose item needs no further reads succeeds)."""
    from tests.contract.drivers import GmailDriver

    world = CappedWorld(env, tmp_path, Cap("max_agent_reads", 3, frozenset(), "imap",
                                           lambda *_: None))  # fmt: skip
    d = world.driver(GmailDriver)
    base = "/gmail/v1/users/me"
    gid = d.http.get(base + "/messages").json()["messages"][0]["id"]  # read 1
    body = {"ids": [gid]} | ({"addLabelIds": ["STARRED"]} if path == "batchModify" else {})
    assert d.http.post(f"{base}/messages/{path}", json=body).status_code == 204
