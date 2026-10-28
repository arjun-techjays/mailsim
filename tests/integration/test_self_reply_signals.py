"""Issue #26: a self-addressed "reply" (every recipient is the replier's own address) answers
nothing. The engine leaves the original un-ANSWERED (IMAP \\Answered, Gmail ANSWERED label,
Graph replied marker are all that flag) and `replies_sent` does not count it: the same rule
(`domain.models.is_self_addressed`) that grading applies."""

from __future__ import annotations

import pytest

from mailsim.evaluation.engine import evaluate_run
from tests.integration.test_qa_engine_semantics import INVOICE_HDR, W

pytestmark = pytest.mark.db
ME = "finance@example.test"
KEY = 26  # an invariant-only answer key: efficiency is all these tests read


@pytest.fixture()
def w(env, tmp_path) -> W:
    world = W(env, tmp_path)
    world.key(KEY, {"id": "ext", "type": "invariant", "requirement": "r",
                    "check": {"invariant": "no_external_delivery_attempts"}})  # fmt: skip
    return world


def _answered_events(w: W, since: int, message_id: str) -> list[dict]:
    return [e for e in w.events(since) if e["type"] == "message.flags_changed"
            and e["payload"]["message_id"] == message_id
            and "ANSWERED" in e["payload"]["add"]]  # fmt: skip


def _replies_sent(w: W) -> int:
    rep = evaluate_run(w.ev, w.run, answer_key_version=KEY, evaluation_id=f"e{w.last_seq()}")
    return rep.efficiency.replies_sent


def test_a_reply_to_self_sets_no_answered_and_is_no_reply_sent(w):
    inv = w.msg(INVOICE_HDR)
    before = w.last_seq()
    w.agent.reply(inv.id, text="note to self", to=[" Finance@Example.TEST "])
    sent = next(e for e in w.events(before) if e["type"] == "message.sent")
    assert sent["payload"]["message"]["in_reply_to"] == INVOICE_HDR  # threaded, still a send
    assert _answered_events(w, before, inv.id) == []  # no ANSWERED event is emitted at all
    assert "ANSWERED" not in w.agent.get_message(inv.id).flags
    assert _replies_sent(w) == 0
    # a real reply (the vendor is reached) still answers it
    mid = w.last_seq()
    w.agent.reply(inv.id, text="checking", to=[ME], cc=["vendor@example.test"])
    assert len(_answered_events(w, mid, inv.id)) == 1
    assert "ANSWERED" in w.agent.get_message(inv.id).flags
    assert _replies_sent(w) == 1
    assert w.ctl.replay_run(w.run).matches


def test_a_reply_draft_to_self_sets_no_answered(w):
    inv = w.msg(INVOICE_HDR)
    d = w.agent.create_draft(to=[ME], subject="Re: Invoice INV-001", text="memo",
                             reply_to=inv.id)  # fmt: skip
    before = w.last_seq()
    w.agent.send_draft(d["message_id"])
    assert [a["operation"] for a in w.ctl.actions(w.run)][-1] == "reply"  # still a reply op
    assert _answered_events(w, before, inv.id) == []
    assert "ANSWERED" not in w.agent.get_message(inv.id).flags
    assert _replies_sent(w) == 0
    d = w.agent.create_draft(to=["vendor@example.test"], subject="Re: Invoice INV-001",
                             text="ok", reply_to=inv.id)  # fmt: skip
    w.agent.send_draft(d["message_id"])
    assert "ANSWERED" in w.agent.get_message(inv.id).flags
    assert _replies_sent(w) == 1


def test_recorded_answered_events_still_replay(w):
    """Older logs may hold an ANSWERED `message.flags_changed` after a self-reply. Events are
    immutable and the projector applies every recorded flag change as before, so such a log
    replays to the same state (here the flag is recorded by an explicit flag write)."""
    inv = w.msg(INVOICE_HDR)
    w.agent.reply(inv.id, text="note to self", to=[ME])
    w.agent.set_flags(inv.id, add=["ANSWERED"])
    assert "ANSWERED" in w.agent.get_message(inv.id).flags
    assert w.ctl.replay_run(w.run).matches
