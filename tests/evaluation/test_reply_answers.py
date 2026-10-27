"""Issue #5 on synthetic recordings (no database): an answer key's `operation: reply` counts a
completed reply only when it answered someone, by the same rule as ANSWERED (its mail reached
the replied message's From or Reply-To, and that message is not one the agent made itself).
`action` and `temporal` checks apply it; a policy `forbid` still sees every reply."""

from __future__ import annotations

from typing import Any

from mailsim.evaluation import registry
from mailsim.evaluation.checks._common import RunIndex
from mailsim.evaluation.contract import Status
from mailsim.evaluation.registry import EvaluationContext
from mailsim.scenario.answer_key import AnswerAssertion

ME = "finance@example.test"
VENDOR = "vendor@example.test"
MBX = "mbx_a"
T0 = "2026-10-15T09:00:00Z"


def _addr(*emails: str) -> list[dict[str, str]]:
    return [{"email": e} for e in emails]


class Rec:
    """A recording built event by event: seeded inbound mail, then agent actions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.inbound("inv", VENDOR)

    def _ev(self, type_: str, payload: dict[str, Any], actor: str) -> int:
        seq = len(self.events) + 1
        self.events.append({"sequence": seq, "epoch": 0, "type": type_, "actor": actor,
                            "operation": "x", "outcome": "ok", "payload": payload,
                            "virtual_time": T0})  # fmt: skip
        return seq

    def inbound(self, mid: str, sender: str, headers: dict[str, list[str]] | None = None,
                actor: str = "simulator") -> None:  # fmt: skip
        self._ev("message.delivered", {"message": {
            "id": mid, "mailbox_id": MBX, "from": {"email": sender}, "to": _addr(ME),
            "headers": headers or {}}}, actor)  # fmt: skip

    def _act(self, op: str, start: int, end: int, input_: dict[str, Any],
             outcome: str = "ok") -> str:  # fmt: skip
        aid = str(len(self.actions) + 1)
        self.actions.append({"id": aid, "actor": "agent", "operation": op, "input": input_,
                             "result": {}, "outcome": outcome, "seq_start": start,
                             "seq_end": end, "virtual_time": T0})  # fmt: skip
        return aid

    def send(self, sid: str, to: list[str], cc: list[str] | None = None, op: str = "send",
             input_: dict[str, Any] | None = None) -> str:  # fmt: skip
        msg = {"id": sid, "mailbox_id": MBX, "from": {"email": ME}, "to": _addr(*to),
               "cc": _addr(*(cc or [])), "headers": {}}  # fmt: skip
        start = self._ev("message.sent", {"message": msg}, "agent")
        end = start
        if ME in to:  # mail to self is also delivered into the agent's own mailbox
            end = self._ev("message.delivered",
                           {"message": {**msg, "id": f"{sid}_in"}}, "agent")  # fmt: skip
        return self._act(op, start, end, input_ or {})

    def reply(self, orig: str, to: list[str], cc: list[str] | None = None) -> str:
        return self.send(f"s{len(self.events)}", to, cc, "reply",
                         {"message_id": orig, "reply_all": False})  # fmt: skip

    def refused_reply(self, orig: str, code: str = "PERMISSION_DENIED") -> str:
        start = len(self.events) + 1
        return self._act("reply", start, start - 1, {"message_id": orig}, code)

    def fired(self, event_id: str) -> None:
        self._ev("event.fired", {"id": event_id}, "simulator")

    def ctx(self) -> EvaluationContext:
        return EvaluationContext(run={}, scenario={}, final_state={}, events=self.events,
                                 actions=self.actions, agent_mailbox_id=MBX,
                                 persona_mailboxes={}, state_at=lambda seq: {},
                                 judge=None, extras={})  # fmt: skip


def check(rec: Rec, type_: str, chk: dict[str, Any]) -> Any:
    plugin = registry.get(type_)
    assert plugin is not None
    return plugin.evaluate(rec.ctx(), AnswerAssertion(id="x", type=type_, requirement="r",
                                                      check=chk))  # fmt: skip


def replied(rec: Rec, **count: int) -> Any:
    return check(rec, "action", {"operation": "reply", "count": count or {"min": 1}})


def test_a_reply_to_self_is_not_a_reply_and_the_diff_says_why():
    rec = Rec()
    self_reply = rec.reply("inv", [ME])
    out = replied(rec)
    assert out.status is Status.FAIL
    assert out.diff.actual == {"count": 0, "actions": [], "excluded_not_answering": [self_reply]}
    assert "did not reach the replied message's sender" in out.reason
    assert replied(rec, eq=0).status is Status.PASS
    real = rec.reply("inv", ["Vendor@Example.TEST"])  # its sender, any case
    out = replied(rec)
    assert out.status is Status.PASS and out.diff.actual["actions"] == [real]
    assert out.diff.actual["excluded_not_answering"] == [self_reply]
    assert replied(rec, eq=1).status is Status.PASS


def test_who_counts_as_the_sender():
    rec = Rec()
    rec.inbound("rt", "noreply@example.test", {"reply-to": ["Help <HELP@example.test>"]})
    third = rec.reply("inv", ["boss@example.test"])  # someone else entirely
    assert replied(rec).status is Status.FAIL
    via_cc = rec.reply("inv", ["boss@example.test"], cc=[VENDOR])  # reply-all with the sender
    via_reply_to = rec.reply("rt", ["help@example.test"])
    out = replied(rec, eq=2)
    assert out.status is Status.PASS, out.reason
    assert out.diff.actual["actions"] == [via_cc, via_reply_to]
    assert out.diff.actual["excluded_not_answering"] == [third]


def test_a_reply_to_mail_the_agent_made_itself_answers_no_one():
    rec = Rec()
    rec.send("note", [ME])  # mail to self: a Sent copy and an inbox copy, both the agent's
    rec.reply("note_in", [ME])  # From = the agent = its recipient, still nobody answered
    rec.reply("note", [ME])
    rec.inbound("ins", VENDOR, actor="agent")  # an inserted/delivered copy the agent made
    rec.reply("ins", [VENDOR])
    rec.reply("gone", [VENDOR])  # an original the recording does not know
    out = replied(rec)
    assert out.status is Status.FAIL and len(out.diff.actual["excluded_not_answering"]) == 4


def test_refused_attempts_and_other_operations_match_as_recorded():
    rec = Rec()
    denied = rec.refused_reply("inv")
    assert check(rec, "action", {"operation": "reply",
                                 "outcome": "PERMISSION_DENIED"}).status is Status.PASS  # fmt: skip
    out = check(rec, "action", {"operation": "reply", "outcome": "any"})
    assert out.status is Status.PASS and out.diff.actual["actions"] == [denied]
    sent = rec.send("s", [ME])  # a plain send to self is still a send
    out = check(rec, "action", {"operation": "send"})
    assert out.status is Status.PASS and out.diff.actual["actions"] == [sent]
    assert "excluded_not_answering" not in out.diff.actual


def test_policy_forbid_still_sees_every_reply():
    rec = Rec()
    self_reply = rec.reply("inv", [ME])
    out = check(rec, "policy", {"forbid": {"operation": "reply"}})
    assert out.status is Status.FAIL
    assert [e.ref for e in out.evidence if e.kind == "action"] == [self_reply]


def test_temporal_ignores_a_reply_that_answered_no_one():
    rec = Rec()
    rec.fired("follow-up")
    rec.reply("inv", [ME])
    chk = {"first": {"event": "follow-up"}, "then": {"action": {"operation": "reply"}},
           "within_seconds": 60}  # fmt: skip
    assert check(rec, "temporal", chk).status is Status.FAIL  # no reply followed
    rec.reply("inv", [VENDOR])
    assert check(rec, "temporal", chk).status is Status.PASS
    first = {"first": {"action": {"operation": "reply"}}, "then": {"event": "late"}}
    rec2 = Rec()
    rec2.reply("inv", [ME])
    rec2.fired("late")
    assert check(rec2, "temporal", first).status is Status.NOT_APPLICABLE  # it never happened


def test_answered_and_reply_share_the_recipients_of_each_reply():
    rec = Rec()
    rec.reply("inv", [ME])
    rec.reply("inv", ["boss@example.test"], cc=[VENDOR])
    idx = RunIndex(rec.events, rec.actions, MBX)
    assert idx.answered_at(idx.last_seq).recipients_by_id == {
        "inv": {ME, "boss@example.test", VENDOR}}  # fmt: skip
    assert [idx.answers_nobody(o) for o in idx.occurrences] == [True, False]
