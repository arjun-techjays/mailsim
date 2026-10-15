"""Shared behavioural contract (spec 5.5), run against every driver in DRIVERS and across
every (writer, reader) pair on the same run."""

from __future__ import annotations

import itertools
import threading

import pytest

from mailsim.domain.errors import ErrorCode
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from tests.contract.drivers import ALL_CAPABILITIES, DRIVERS, NeutralDriver
from tests.scenarios import write_variant

pytestmark = pytest.mark.db


def _open(s):
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500


class World:
    def __init__(self, env, tmp_path):
        self.env = env
        self.ctl = Controller(env.engine, env.scope)
        self.ev = EvaluatorView(env.engine, env.scope)
        self.run = self.ctl.create_run(env.version(write_variant(tmp_path, _open)))
        self.ctl.start_run(self.run)
        self.agent_mb = self.ctl.mailbox_id_for(self.run, "finance_manager")
        self.vendor_mb = self.ctl.mailbox_id_for(self.run, "vendor")

    def driver(self, cls, mailbox=None):
        return cls(
            AgentMailbox(self.env.engine, self.env.scope, self.run, mailbox or self.agent_mb)
        )


@pytest.fixture()
def world(env, tmp_path):
    return World(env, tmp_path)


@pytest.fixture(params=DRIVERS, ids=lambda d: d.name)
def drv(request, world):
    return request.param


def requires(cls, cap):
    if cap not in cls.capabilities:
        pytest.skip(f"{cls.name} does not declare capability {cap!r}")


def test_send_isolation(world, drv):
    requires(drv, "send")
    d = world.driver(drv)
    header = d.send(["vendor@example.test"], "contract-send", "body")
    # the recipient's copy exists only as simulator state (read via the evaluator role)
    vendor_msgs = [
        m for m in world.ev.state(world.run)["messages"] if m["mailbox_id"] == world.vendor_mb
    ]
    assert [m["message_id_header"] for m in vendor_msgs] == [header]
    with pytest.raises(Exception) as denied:  # and the agent cannot read another mailbox
        world.driver(drv, world.vendor_mb).list("inbox")
    assert d.error_code(denied.value) == ErrorCode.PERMISSION_DENIED
    with pytest.raises(Exception) as ei:
        d.send(["someone@real-domain.example"], "escape", "x")
    assert d.error_code(ei.value) == ErrorCode.EXTERNAL_DELIVERY_BLOCKED
    types = {e["type"] for e in world.ev.events(world.run)}
    assert not types & {"delivery.external_attempt", "delivery.sunk"}


@pytest.mark.parametrize(("writer", "reader"), list(itertools.product(DRIVERS, DRIVERS)),
                         ids=lambda d: d.name)  # fmt: skip
def test_read_after_write_across_drivers(world, writer, reader):
    w, r = world.driver(writer), world.driver(reader)
    target = w.list("inbox")[0]
    w.flag(target.ref, ["FLAGGED"])
    w.label(target.ref, ["contract-label"])
    w.move(target.ref, "archive")
    seen = next(m for m in r.list() if m.canonical_id == target.canonical_id)
    assert seen.folder == "archive" and "FLAGGED" in seen.flags and "contract-label" in seen.labels
    header = w.send(["finance@example.test"], "rw-self", "x")
    assert header in {m.message_id_header for m in r.list("inbox")}


def test_identity_is_stable(world, drv):
    d = world.driver(drv)
    first = d.list()[0]
    again = d.get(first.ref)
    assert (again.ref, again.canonical_id, again.message_id_header, again.thread_ref) == (
        first.ref, first.canonical_id, first.message_id_header, first.thread_ref)  # fmt: skip
    d.flag(first.ref, ["SEEN"])
    assert d.get(first.ref).ref == first.ref, "state changes never change the id"


def test_threading_consistency(world, drv):
    requires(drv, "reply")
    d = world.driver(drv)
    original = d.list("inbox")[0]
    d.reply(original.ref, "ack")
    thread = d.thread(original.thread_ref)
    assert original.canonical_id in {m.canonical_id for m in thread}
    replies = [m for m in thread if m.folder == "sent"]
    assert replies and all(m.in_reply_to == original.message_id_header for m in replies)


def test_error_semantics(world, drv):
    d = world.driver(drv)
    with pytest.raises(Exception) as ei:
        d.get("msg_does_not_exist")
    assert d.error_code(ei.value) == ErrorCode.NOT_FOUND
    with pytest.raises(Exception) as ei:
        d.move(d.list()[0].ref, "no-such-folder")
    assert d.error_code(ei.value) == ErrorCode.NOT_FOUND


def test_pagination_has_no_dupes_or_gaps(world, drv):
    requires(drv, "page")
    for i in range(5):
        world.ctl.inject(world.run, world.agent_mb, {"from": "vendor@example.test",
                         "to": ["finance@example.test"], "subject": f"pg{i}", "text": "t"})  # fmt: skip
    d = world.driver(drv)
    seen, cursor = [], None
    while True:
        items, cursor = d.page(2, cursor)
        seen += [m.canonical_id for m in items]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == len(d.list())


def test_concurrent_writes_follow_documented_rules(world, drv):
    d = world.driver(drv)
    target = d.list()[0]
    errors = []

    def w(flag):
        try:
            world.driver(drv).flag(target.ref, [flag])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=w, args=(f,)) for f in ("SEEN", "FLAGGED", "ANSWERED")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    assert {"SEEN", "FLAGGED", "ANSWERED"} <= d.get(target.ref).flags
    seqs = [e["sequence"] for e in world.ev.events(world.run)]
    assert seqs == list(range(1, len(seqs) + 1))


def test_reset_restores_baseline(world, drv):
    d = world.driver(drv)
    before = sorted(m.message_id_header for m in d.list())
    d.send(["finance@example.test"], "extra", "x")
    assert len(d.list()) > len(before)
    world.ctl.reset_run(world.run)
    world.ctl.start_run(world.run)
    assert sorted(m.message_id_header for m in d.list()) == before


def test_change_feed(world, drv):
    requires(drv, "changes")
    d = world.driver(drv)
    _, cursor = d.changes(0)
    target = d.list()[0]
    d.flag(target.ref, ["SEEN"])
    changed, _ = d.changes(cursor)
    assert changed == [target.canonical_id]


def test_capability_discovery(world, drv):
    """Every capability a driver does not declare must be refused explicitly. Drivers with
    partial profiles add a `probe(cap)` that performs the native call for that capability."""
    d = world.driver(drv)
    for cap in sorted(ALL_CAPABILITIES - drv.capabilities):  # pragma: no cover - neutral has all
        with pytest.raises(Exception) as ei:
            d.probe(cap)  # type: ignore[attr-defined]
        assert d.error_code(ei.value) == ErrorCode.UNSUPPORTED_OPERATION, cap
    assert d.search("purchase order")


def test_observability_every_mutation_is_audited(world, drv):
    d = world.driver(drv)
    before = len(world.ctl.actions(world.run))
    target = d.list()[0]
    d.flag(target.ref, ["SEEN"])
    d.delete(target.ref)
    new = world.ctl.actions(world.run)[before:]
    ops = [(a["actor"], a["operation"], a["outcome"]) for a in new]
    assert ("agent", "flag", "ok") in ops and ("agent", "delete", "ok") in ops


def test_agent_cannot_see_hidden_state(world, drv):
    d = world.driver(drv)
    first = d.list("inbox")[0]
    d.reply(first.ref, "We will verify the bank details independently.")
    world.ctl.advance(world.run, 700)
    key = world.ev.answer_key(world.run)  # inline-derived key from the example scenario
    secrets = [a["requirement"] for a in key["spec"]["answer_key"]["assertions"]]
    visible = " ".join(m.body + m.subject for m in d.list()).casefold()
    assert visible, "the driver must expose message text for this check to mean anything"
    assert not [s for s in secrets if s.casefold() in visible]


def test_neutral_driver_is_registered():
    assert NeutralDriver in DRIVERS
