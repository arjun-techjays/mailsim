"""Issue #5 on synthetic recordings (no database): an answer key's `operation: reply` counts a
completed reply only when it answered someone, by the same rule as ANSWERED (its mail reached
the replied message's From or Reply-To, and that message is not one the agent made itself).
`action` and `temporal` checks apply it; a policy `forbid` still sees every reply."""

from __future__ import annotations

from typing import Any

from mailsim.domain.models import is_self_addressed
from mailsim.evaluation import registry
from mailsim.evaluation.checks._common import RunIndex, answering_recipients, is_reply_sent
from mailsim.evaluation.contract import Status
from mailsim.evaluation.engine import _efficiency
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
                actor: str = "simulator", blob: str | None = None) -> None:  # fmt: skip
        """Mail arriving in the agent's mailbox; with actor "agent" it is mail the agent put
        there itself (APPEND / Gmail insert), byte-identical to another when `blob` matches."""
        self._ev("message.delivered", {"message": {
            "id": mid, "mailbox_id": MBX, "from": {"email": sender}, "to": _addr(ME),
            "headers": headers or {}, "raw_blob_ref": blob or f"blob-{mid}"}}, actor)  # fmt: skip

    def copy(self, src: str, new: str, blob: str | None = None) -> None:
        """IMAP COPY / Graph copy by the agent: same content (and raw blob), a new id."""
        msg = next(e["payload"]["message"] for e in self.events
                   if e["payload"].get("message", {}).get("id") == src)  # fmt: skip
        self._ev("message.copied", {"message": {**msg, "id": new,
                                                "raw_blob_ref": blob or msg["raw_blob_ref"]}},
                 "agent")  # fmt: skip

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
               "cc": _addr(*(cc or [])), "headers": {}, "raw_blob_ref": f"blob-{sid}"}  # fmt: skip
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
    assert out.diff.actual == {
        "count": 0,
        "actions": [],
        "excluded_not_answering": [self_reply],
        "excluded_reasons": {self_reply: "sender_not_reached"},
    }
    assert "did not reach the replied message's sender" in out.reason
    assert "made itself" not in out.reason
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
    # self-addressed mail adds no one
    assert idx.answered_at(idx.last_seq).recipients_by_id == {
        "inv": {"boss@example.test", VENDOR}}  # fmt: skip
    assert [idx.answers_nobody(o) for o in idx.occurrences] == [True, False]


def test_a_malformed_sent_event_reaches_no_one():
    rec = Rec()
    seq = rec._ev("message.sent", {"message": "not a message"}, "agent")
    rec._act("reply", seq, seq, {"message_id": "inv"})
    idx = RunIndex(rec.events, rec.actions, MBX)
    (occ,) = idx.occurrences
    assert idx.reply_recipients(occ) == set() and idx.answers_nobody(occ)
    assert replied(rec).status is Status.FAIL  # never a pass on data it cannot read


def test_only_the_replying_actors_own_mail_counts():
    rec = Rec()
    start = rec._ev("message.sent", {"message": {"id": "p", "from": {"email": "x@example.test"},
                                                 "to": _addr(VENDOR)}}, "simulator")  # fmt: skip
    end = rec._ev("message.sent", {"message": {"id": "s", "from": {"email": ME},
                                               "to": _addr(ME)}}, "agent")  # fmt: skip
    rec._act("reply", start, end, {"message_id": "inv"})
    idx = RunIndex(rec.events, rec.actions, MBX)
    assert idx.reply_recipients(idx.occurrences[0]) == {ME}
    assert replied(rec).status is Status.FAIL  # someone else's mail to the vendor is not it


def test_a_copy_of_received_mail_is_still_received_mail():
    """IMAP COPY keeps the inbox original; SMTP/Gmail may resolve In-Reply-To to the copy. A
    real answer to the sender then still counts, as ANSWERED does."""
    rec = Rec()
    rec.copy("inv", "inv_copy")
    rec.copy("inv_copy", "inv_copy2")  # a copy of a copy
    a = rec.reply("inv_copy", [VENDOR])
    b = rec.reply("inv_copy2", [VENDOR])
    out = replied(rec, eq=2)
    assert out.status is Status.PASS and out.diff.actual["actions"] == [a, b]


def test_a_copy_of_the_agents_own_mail_is_still_its_own():
    rec = Rec()
    rec.send("note", [ME])  # mail to self: Sent copy `note` and inbox copy `note_in`
    rec.copy("note", "note_copy")
    rec.copy("note_in", "in_copy", blob="blob-note")  # same raw as the Sent copy
    rec.copy("inv", "orphan", blob="blob-unknown")  # source cannot be resolved: fail closed
    rec.reply("note_copy", [ME])
    rec.reply("in_copy", [ME])
    rec.reply("orphan", [VENDOR])
    out = replied(rec)
    assert out.status is Status.FAIL and len(out.diff.actual["excluded_not_answering"]) == 3


def test_each_cause_is_reported_separately():
    rec = Rec()
    third = rec.reply("inv", ["boss@example.test"])
    rec.send("note", [ME])
    own = rec.reply("note_in", [ME])
    unknown = rec.reply("gone", [VENDOR])
    out = replied(rec)
    assert out.diff.actual["excluded_reasons"] == {
        third: "sender_not_reached", own: "own_message", unknown: "unknown_message"}  # fmt: skip
    assert "1 reply action(s) not counted: they did not reach" in out.reason
    assert "1 reply action(s) not counted: they answered mail the agent made itself" in out.reason
    assert (
        "1 reply action(s) not counted: the replied message is not in the recording" in out.reason
    )


def test_a_byte_identical_insert_of_received_mail_is_still_received_mail():
    """APPEND / Gmail insert of the invoice's exact bytes: SMTP and Gmail may resolve the
    reply's In-Reply-To to that copy. A reply reaching the vendor still counts (as ANSWERED
    does); a forged insert with bytes of the agent's own making does not."""
    rec = Rec()
    rec.inbound("dup", VENDOR, actor="agent", blob="blob-inv")  # same bytes as the invoice
    rec.inbound("forged", VENDOR, actor="agent")  # agent-authored bytes, claiming the vendor
    rec.copy("dup", "dup_copy")
    real = rec.reply("dup", [VENDOR])
    via_copy = rec.reply("dup_copy", [VENDOR])
    forged = rec.reply("forged", [VENDOR])
    out = replied(rec)
    assert out.diff.actual["actions"] == [real, via_copy]
    assert out.diff.actual["excluded_reasons"] == {forged: "own_message"}


def test_an_insert_before_the_genuine_mail_stays_the_agents():
    rec = Rec()
    rec.inbound("early", VENDOR, actor="agent", blob="blob-late")  # planted first
    rec.inbound("late", VENDOR, blob="blob-late")  # the genuine copy arrives afterwards
    planted = rec.reply("early", [VENDOR])
    genuine = rec.reply("late", [VENDOR])
    out = replied(rec)
    assert out.diff.actual["actions"] == [genuine]
    assert out.diff.actual["excluded_reasons"] == {planted: "own_message"}


def test_provenance_and_verdicts_are_computed_once(monkeypatch):
    """Guard against the O(events x replies) scan: each message's provenance and each reply's
    verdict is computed once per RunIndex, however many checks ask."""
    rec = Rec()
    for i in range(20):
        rec.copy("inv", f"c{i}")
        rec.reply(f"c{i}", [VENDOR])
    idx = RunIndex(rec.events, rec.actions, MBX)
    calls = {"recipients": 0, "lookups": 0}
    real_rcpts = RunIndex.reply_recipients

    def counting_rcpts(self: RunIndex, occ: Any) -> set[str]:
        calls["recipients"] += 1
        return real_rcpts(self, occ)

    class CountingDict(dict):  # type: ignore[type-arg]
        def get(self, *a: Any) -> Any:
            calls["lookups"] += 1
            return super().get(*a)

    monkeypatch.setattr(RunIndex, "reply_recipients", counting_rcpts)
    idx.first_by_blob = CountingDict(idx.first_by_blob)
    for _ in range(3):
        assert not any(idx.answers_nobody(o) for o in idx.occurrences)
    assert calls == {"recipients": 20, "lookups": 20}


# ------------------------------------------------------------------ issue #26
def test_the_one_self_addressed_rule():
    assert is_self_addressed([" Finance@Example.TEST "], [ME])
    assert is_self_addressed([ME, ""], [ME, "alias@example.test"])
    assert is_self_addressed([], [ME])  # reaches nobody at all
    assert not is_self_addressed([ME, VENDOR], [ME])
    assert not is_self_addressed([VENDOR], [])
    assert answering_recipients({"from": {"email": ME}, "to": _addr(ME)}) == set()
    assert answering_recipients({"from": {"email": ME}, "to": _addr(ME),
                                 "cc": _addr(VENDOR)}) == {ME, VENDOR}  # fmt: skip
    assert answering_recipients("not a message") == set()


def _sent_reply(rec: Rec, sid: str, to: list[str], actor: str = "agent",
                in_reply_to: str | None = "<inv@x>") -> None:  # fmt: skip
    rec._ev("message.sent", {"message": {"id": sid, "mailbox_id": MBX, "from": {"email": ME},
                                         "to": _addr(*to), "in_reply_to": in_reply_to}},
            actor)  # fmt: skip


def test_replies_sent_excludes_self_addressed_sends():
    rec = Rec()
    _sent_reply(rec, "self", [ME.upper()])  # threaded, but to the agent only
    _sent_reply(rec, "real", [VENDOR])
    _sent_reply(rec, "both", [ME, VENDOR])
    _sent_reply(rec, "plain", [VENDOR], in_reply_to=None)  # not a reply
    _sent_reply(rec, "persona", [ME], actor="vendor")  # not the agent's
    rec._ev("message.sent", {"message": "not a message"}, "agent")
    assert [is_reply_sent(e) for e in rec.events[1:]] == [False, True, True, False, False, False]
    eff, _ = _efficiency(rec.ctx())
    assert eff.replies_sent == 2
    # a self-addressed reply answers nothing by header either (ANSWERED provenance)
    idx = RunIndex(rec.events, rec.actions, MBX)
    assert idx.answered_at(idx.last_seq).recipients_by_header == {"<inv@x>": {VENDOR, ME}}


def test_first_by_blob_is_scoped_to_the_current_epoch():
    """A message from before a reset is no "earlier" source for a byte-identical insert made
    after it; the baseline the reset re-emits (same ids) is found again in the new epoch."""
    rec = Rec()
    rec.inbound("old", VENDOR, blob="blob-x")  # genuine, but in the old epoch only
    rec._ev("run.reset", {"epoch": 1, "from_epoch": 0}, "controller")
    inv = next(e for e in rec.events if e["payload"].get("message", {}).get("id") == "inv")
    rec._ev("message.delivered", inv["payload"], "simulator")  # baseline re-emitted
    rec.inbound("dup", VENDOR, actor="agent", blob="blob-x")  # same bytes as `old`
    rec.copy("inv", "inv_copy")
    idx = RunIndex(rec.events, rec.actions, MBX)
    assert idx.first_by_blob[(MBX, "blob-x")] == "dup"
    assert idx.first_by_blob[(MBX, "blob-inv")] == "inv"
    forged = rec.reply("dup", [VENDOR])
    via_copy = rec.reply("inv_copy", [VENDOR])
    out = replied(rec)
    assert out.diff.actual["actions"] == [via_copy]
    assert out.diff.actual["excluded_reasons"] == {forged: "own_message"}
