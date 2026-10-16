"""Regression tests for the QA engine-semantics workstream (QA-A1).

B-1 internal lookups never become agent errors · B-2 blocked external attempts are evidence ·
B-3 a reply/forward draft is sent as ONE reply/forward · B-5 no forged Sent/Drafts placement ·
B-7 persona replies stamped at their due time · UI-4 persona display names in MIME headers ·
checks-off idle runs are INCONCLUSIVE.
"""

from __future__ import annotations

from datetime import timedelta
from email import message_from_bytes
from typing import Any

import pytest

from mailsim.domain.clock import parse_ts
from mailsim.domain.errors import (
    InvalidRequest,
    NotFound,
    PermissionDenied,
    SimulationLimitExceeded,
)
from mailsim.engine.core import CONTROLLER
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.evaluation.engine import evaluate_run
from mailsim.llm.gateway import LLMGateway
from mailsim.personas.runtime import PersonaRuntime, advance_clock, tick_clock
from mailsim.scenario.answer_key import AnswerKeyDoc
from mailsim.scenario.answer_keys import create_answer_key_version
from tests.llm.conftest import ScriptedProvider
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
INVOICE_HDR = "<invoice-001@example.test>"


def open_all(s: dict[str, Any]) -> None:
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 200


class W:
    def __init__(self, env, tmp_path, mutate=open_all) -> None:
        self.env = env
        self.ctl = Controller(env.engine, env.scope)
        self.ev = EvaluatorView(env.engine, env.scope)
        self.run = self.ctl.create_run(env.version(write_variant(tmp_path, mutate)))
        self.ctl.start_run(self.run)
        self.fm = self.ctl.mailbox_id_for(self.run, "finance_manager")
        self.vendor = self.ctl.mailbox_id_for(self.run, "vendor")
        self.agent = AgentMailbox(env.engine, env.scope, self.run, self.fm)

    def msg(self, header: str, folder: str = "inbox") -> Any:
        return next(m for m in self.agent.list_messages(folder=folder, page_size=200).items
                    if m.message_id_header == header)  # fmt: skip

    def events(self, since: int = 0) -> list[dict[str, Any]]:
        return [e for e in self.ev.events(self.run) if e["sequence"] > since]

    def last_seq(self) -> int:
        return int(self.ev.events(self.run)[-1]["sequence"])

    def agent_actions(self, since: int = 0) -> list[tuple[str, str]]:
        return [(a["operation"], a["outcome"]) for a in self.ctl.actions(self.run)[since:]
                if a["actor"] == "agent"]  # fmt: skip

    def key(self, version: int, *assertions: dict[str, Any]) -> int:
        doc = AnswerKeyDoc.model_validate({"answer_key_version": "1.0", "answer_key": {
            "scenario": "finance.invoice-bank-change", "version": version,
            "assertions": list(assertions)}})  # fmt: skip
        with self.env.session_factory.begin() as s:
            create_answer_key_version(s, self.env.scope, doc, self.env.blobs)
        return version


@pytest.fixture()
def w(env, tmp_path) -> W:
    return W(env, tmp_path)


# ------------------------------------------------------------------ B-1 internal lookups
def test_multi_kind_resolution_and_present_messages_record_no_errors(w):
    inv = w.msg(INVOICE_HDR)
    draft = w.agent.create_draft(to=["vendor@example.test"], subject="d", text="x")
    drafted = w.agent.get_message(draft["message_id"])
    n = len(w.ctl.actions(w.run))
    graph_draft_id = drafted.provider_ids["graph_draft_id"]
    assert w.agent.resolve_external_id("graph", ("message", "draft"),
                                       graph_draft_id) == draft["message_id"]  # fmt: skip
    present = w.agent.present_messages([inv.id, "msg_gone", inv.id])
    assert list(present) == [inv.id] and present[inv.id].subject == inv.subject
    assert w.agent.present_messages([]) == {}
    assert w.agent_actions(n) == [("read", "ok"), ("read", "ok"), ("read", "ok")]
    # a genuinely unknown id is still the agent's NOT_FOUND (one recorded read)
    with pytest.raises(NotFound, match="message/draft"):
        w.agent.resolve_external_id("graph", ("message", "draft"), "AAMk-nope")
    assert w.agent_actions(n)[-1] == ("read", "NOT_FOUND")
    with pytest.raises(InvalidRequest):
        w.agent.resolve_external_id("graph", (), "x")
    with pytest.raises(InvalidRequest):
        w.agent.resolve_external_id("graph", ("message", "label"), "x")


# ------------------------------------------------------- B-2 blocked attempts are evidence
def test_envelope_blocked_attempt_is_recorded_without_budget_or_delivery(w):
    before = w.last_seq()
    budget = w.ctl.run_info(w.run)["agent_actions"]
    w.agent.record_external_blocked(["Boss@Real.example"])
    new = w.events(before)
    assert [(e["type"], e["actor"], e["operation"], e["outcome"]) for e in new] == [
        ("delivery.external_blocked", "agent", "send", "EXTERNAL_DELIVERY_BLOCKED")]  # fmt: skip
    assert new[0]["payload"]["recipients"] == ["boss@real.example"]
    act = w.ctl.actions(w.run)[-1]
    assert (act["operation"], act["outcome"], act["input"]["stage"]) == (
        "send", "EXTERNAL_DELIVERY_BLOCKED", "envelope")  # fmt: skip
    assert w.ctl.run_info(w.run)["agent_actions"] == budget  # nothing was executed
    # only the agent's own mailbox
    other = AgentMailbox(w.env.engine, w.env.scope, w.run, w.vendor)
    with pytest.raises(NotFound):
        other.record_external_blocked(["x@real.example"])
    with pytest.raises(NotFound):
        w.env.engine.record_external_blocked(w.env.scope, w.run, "mbx_none", ["x@real.example"])


# ----------------------------------------------------------- B-3 reply / forward drafts
def test_reply_draft_send_is_one_reply_with_reply_semantics(w):
    inv = w.msg(INVOICE_HDR)
    before = w.last_seq()
    d = w.agent.create_draft(to=["vendor@example.test"], subject="Re: Invoice INV-001",
                             text="We will verify the bank details.", reply_to=inv.id)  # fmt: skip
    drafted = w.agent.get_message(d["message_id"])
    assert drafted.in_reply_to == INVOICE_HDR and drafted.references == [INVOICE_HDR]
    assert "origin" not in drafted.provider_ids  # bookkeeping stays engine-internal
    upd = w.agent.update_draft(d["message_id"], to=["vendor@example.test"],
                               subject="Re: Invoice INV-001", text="Verifying by phone.")  # fmt: skip
    out = w.agent.send_draft(upd["message_id"])
    new = w.events(before)
    counted = [e["payload"]["operation"] for e in new if e["type"] == "agent.action_counted"]
    assert counted == ["draft", "draft", "reply"]  # one reply, no plain `send`
    sent = next(e for e in new if e["type"] == "message.sent")
    assert sent["operation"] == "reply" and sent["payload"]["message"]["in_reply_to"] == INVOICE_HDR
    assert "ANSWERED" in w.agent.get_message(inv.id).flags
    armed = [e["payload"]["id"] for e in new if e["type"] == "event.armed"]
    assert armed == ["vendor-bank-change"]  # the send_reply trigger fired
    act = next(a for a in w.ctl.actions(w.run) if a["operation"] == "reply")
    assert (act["operation"], act["input"]) == (
        "reply", {"draft_message_id": upd["message_id"], "draft_of": "reply"})  # fmt: skip
    assert out["delivered_message_ids"]
    # replay reproduces the state from the recorded events alone
    assert w.ctl.replay_run(w.run).matches


def test_forward_draft_carries_attachments_and_sends_as_forward(w):
    inv = w.msg(INVOICE_HDR)
    assert inv.attachments
    d = w.agent.create_draft(to=["vendor@example.test"], subject="Fwd: Invoice INV-001",
                             text="fyi", forward_of=inv.id)  # fmt: skip
    assert len(w.agent.get_message(d["message_id"]).attachments) == len(inv.attachments)
    before = w.last_seq()
    out = w.agent.send_draft(d["message_id"])
    sent = next(e for e in w.events(before) if e["type"] == "message.sent")
    assert sent["operation"] == "forward" and len(sent["payload"]["message"]["attachments"]) == 1
    assert [a["operation"] for a in w.ctl.actions(w.run)][-1] == "forward"
    assert "ANSWERED" not in w.agent.get_message(inv.id).flags
    assert w.agent.get_message(out["message_id"]).folder == "Sent"


def test_draft_origin_rules(w):
    inv = w.msg(INVOICE_HDR)
    with pytest.raises(InvalidRequest, match="at most one"):
        w.agent.create_draft(to=["vendor@example.test"], subject="x", reply_to=inv.id,
                             forward_of=inv.id)  # fmt: skip
    plain = w.agent.create_draft(to=["vendor@example.test"], subject="x")
    with pytest.raises(InvalidRequest, match="is a draft"):
        w.agent.create_draft(to=["vendor@example.test"], subject="x",
                             reply_to=plain["message_id"])  # fmt: skip
    with pytest.raises(NotFound):
        w.agent.create_draft(to=["vendor@example.test"], subject="x", reply_to="msg_nope")
    # explicit reply headers are kept; the original is gone by send time -> a plain send
    d = w.agent.create_draft(to=["vendor@example.test"], subject="x", reply_to=inv.id,
                             in_reply_to="<other@example.test>")  # fmt: skip
    assert w.agent.get_message(d["message_id"]).in_reply_to == "<other@example.test>"
    w.agent.delete(inv.id)
    w.agent.send_draft(d["message_id"])
    assert w.ctl.actions(w.run)[-1]["operation"] == "send"
    with pytest.raises(NotFound):  # unknown draft: the command itself reports it
        w.agent.send_draft("msg_nope")


def test_reply_draft_needs_reply_permission_not_send(env, tmp_path):
    def no_reply(s: dict[str, Any]) -> None:
        open_all(s)
        s["agent"]["allowed_operations"] = ["read", "draft", "send"]

    w = W(env, tmp_path, no_reply)
    inv = w.msg(INVOICE_HDR)
    d = w.agent.create_draft(to=["vendor@example.test"], subject="Re", reply_to=inv.id)
    with pytest.raises(PermissionDenied):
        w.agent.send_draft(d["message_id"])
    assert w.ctl.actions(w.run)[-1]["operation"] == "reply"


# --------------------------------------------------------------- B-5 forged placement
def test_received_mail_cannot_be_moved_or_copied_into_sent_or_drafts(w):
    inv = w.msg(INVOICE_HDR)
    for op in (w.agent.move, w.agent.copy):
        for folder in ("sent", "Drafts"):
            with pytest.raises(PermissionDenied, match="cannot be placed"):
                op(inv.id, folder)
    assert w.agent_actions()[-1] == ("copy", "PERMISSION_DENIED")
    # the agent's own sent mail may leave Sent and come back; a draft may return to Drafts
    sent = w.agent.send(to=["vendor@example.test"], subject="s", text="t")
    w.agent.move(sent["message_id"], "archive")
    w.agent.move(sent["message_id"], "sent")
    w.agent.copy(sent["message_id"], "sent")
    d = w.agent.create_draft(to=["vendor@example.test"], subject="d")
    w.agent.trash(d["message_id"])
    w.agent.move(d["message_id"], "drafts")
    with pytest.raises(PermissionDenied, match="a draft cannot be placed in Sent"):
        w.agent.move(d["message_id"], "sent")
    w.agent.move(inv.id, "inbox")  # same folder: never refused
    # the operator is not bound by the agent rule (used below to fake a legacy recording)
    w.env.engine.move(w.env.scope, w.run, w.fm, inv.id, "sent", actor=CONTROLLER)


def test_sent_evidence_uses_provenance_not_folder(w):
    inv = w.msg(INVOICE_HDR)
    w.agent.reply(inv.id, text="verify INV-001")
    # a received message placed in Sent / Drafts by other means (legacy recordings)
    po = next(m for m in w.agent.list_messages(folder="inbox").items if m.id != inv.id)
    w.env.engine.move(w.env.scope, w.run, w.fm, inv.id, "sent", actor=CONTROLLER)
    w.env.engine.move(w.env.scope, w.run, w.fm, po.id, "drafts", actor=CONTROLLER)
    v = w.key(
        9,
        {"id": "sent", "type": "state", "requirement": "r",
         "check": {"where": {"folder": "sent"}, "count": {"eq": 1}}},
        {"id": "sent-all", "type": "state", "requirement": "r",
         "check": {"where": {"folder": "sent", "include_agent_created": True},
                   "count": {"eq": 2}}},
        {"id": "drafts", "type": "state", "requirement": "r",
         "check": {"where": {"folder": "drafts"}, "count": {"eq": 0}}},
        {"id": "content", "type": "content", "requirement": "r",
         "check": {"where": {"folder": "sent"}, "contains": ["INV-001"], "count": {"eq": 1}}},
    )  # fmt: skip
    rep = evaluate_run(w.ev, w.run, answer_key_version=v, evaluation_id="e1")
    outs = {o.assertion_id: o for o in rep.outcomes}
    assert all(o.status.value == "PASS" for o in outs.values()), {
        k: o.reason for k, o in outs.items()}  # fmt: skip
    assert outs["sent"].diff.actual["excluded_agent_created"] == [inv.id]


# ------------------------------------------------------------ UI-4 persona display names
def test_persona_display_names_in_mime_headers(env, tmp_path):
    def named(s: dict[str, Any]) -> None:
        open_all(s)
        for p in s["personas"]:
            p["name"] = {"finance_manager": "Fin, Manager", "vendor": "Véndor Co"}[p["id"]]

    w = W(env, tmp_path, named)
    out = w.agent.send(to=["VENDOR@example.test"], subject="hi", text="t")
    raw = w.agent.get_raw(out["message_id"])
    assert raw is not None
    hdr = message_from_bytes(raw)
    assert hdr["From"] == '"Fin, Manager" <finance@example.test>'
    assert "<vendor@example.test>" in hdr["To"] and "=?utf-8?" in hdr["To"]
    got = w.agent.get_message(out["message_id"])
    assert got.from_.email == "finance@example.test" and got.from_.name == "Fin, Manager"
    assert [(a.email, a.name) for a in got.to] == [("vendor@example.test", "Véndor Co")]
    d = w.agent.create_draft(to=["vendor@example.test"], subject="d")
    assert w.agent.get_message(d["message_id"]).from_.name == "Fin, Manager"
    w.ctl.inject(w.run, w.fm, {"from": "vendor@example.test", "to": ["finance@example.test"],
                               "subject": "inj", "text": "x"})  # fmt: skip
    inj = next(m for m in w.agent.list_messages(folder="inbox").items if m.subject == "inj")
    assert inj.from_.name == "Véndor Co" and inj.to[0].name == "Fin, Manager"
    assert w.ctl.replay_run(w.run).matches


# --------------------------------------------------------- B-7 persona replies on time
def timed(tmp_path_events: list[dict[str, Any]]):
    def tweak(s: dict[str, Any]) -> None:
        open_all(s)
        by_id = {p["id"]: p for p in s["personas"]}
        by_id["vendor"]["behavior"].update(
            {"mode": "scripted", "response_delay_seconds": 60,
             "script": [{"on": {"subject_contains": "quote"}, "reply": {"body": "Quote attached."}}]}
        )  # fmt: skip
        s["evaluation"]["limits"]["max_virtual_duration_seconds"] = 7200
        s["events"] = tmp_path_events

    return tweak


def test_persona_replies_are_stamped_at_their_due_time(env, tmp_path):
    events = [{"id": "half-hour", "trigger": {"type": "after_seconds", "seconds": 1800},
               "event": {"type": "email.receive", "from": "vendor", "to": "finance_manager",
                         "subject": "half-hour", "body": "ping"}}]  # fmt: skip
    w = W(env, tmp_path, timed(events))
    rt = PersonaRuntime(w.ctl, LLMGateway(ScriptedProvider("unused")))
    start = parse_ts(w.ctl.run_info(w.run)["virtual_time"])
    w.agent.send(to=["vendor@example.test"], subject="your quote", text="see quote")
    assert rt.next_due(w.run) == start + timedelta(seconds=60)
    assert rt.next_due(w.run, after=start + timedelta(seconds=60)) is None
    before = w.last_seq()
    out = advance_clock(w.ctl, rt, w.run, 3000)
    assert out == {"fired": ["half-hour"], "virtual_time": (start + timedelta(seconds=3000))
                   .isoformat().replace("+00:00", "Z"), "persona_replies": 1}  # fmt: skip
    inbox = {m.subject: m for m in w.agent.list_messages(folder="inbox").items}
    assert inbox["Re: your quote"].received_at == start + timedelta(seconds=60)
    assert inbox["half-hour"].received_at == start + timedelta(seconds=1800)
    raw = w.agent.get_raw(inbox["Re: your quote"].id)
    assert raw is not None and b"09:01:00" in raw  # Date header = due time
    # the log is in time order: the reply precedes the scheduled event
    types = [(e["type"], e["virtual_time"]) for e in w.events(before)
             if e["type"] in ("persona.generated", "event.fired")]  # fmt: skip
    assert [t for t, _ in types] == ["persona.generated", "event.fired"]
    assert [parse_ts(v) for _, v in types] == sorted(parse_ts(v) for _, v in types)
    advances = [a for a in w.ctl.actions(w.run) if a["operation"] == "advance"]
    assert [a["input"].get("stop_at") is not None for a in advances] == [True, True]
    assert w.ctl.replay_run(w.run).matches
    # a tick at a quiet moment answers nothing and moves nothing
    assert tick_clock(w.ctl, rt, w.run) == {"fired": [], "virtual_time": out["virtual_time"],
                                            "persona_replies": 0}  # fmt: skip
    w.ctl.cancel_run(w.run)
    assert rt.next_due(w.run) is None  # nothing is due in a finished run


def test_advance_clock_validates_the_whole_move_first(env, tmp_path):
    w = W(env, tmp_path, timed([]))
    rt = PersonaRuntime(w.ctl, LLMGateway(ScriptedProvider("unused")))
    w.agent.send(to=["vendor@example.test"], subject="your quote", text="?")
    vt = w.ctl.run_info(w.run)["virtual_time"]
    with pytest.raises(SimulationLimitExceeded):
        advance_clock(w.ctl, rt, w.run, 7201)
    assert w.ctl.run_info(w.run)["virtual_time"] == vt  # nothing moved, nothing answered
    with pytest.raises(InvalidRequest):
        advance_clock(w.ctl, rt, w.run, float("inf"))
    assert advance_clock(w.ctl, None, w.run, 10)["persona_replies"] == 0
    # backlog already due before the advance is answered at the first stop (now)
    out = advance_clock(w.ctl, rt, w.run, 100)
    assert out["persona_replies"] == 1
    reply = next(m for m in w.agent.list_messages(folder="inbox").items
                 if m.subject == "Re: your quote")  # fmt: skip
    assert reply.received_at == parse_ts(vt) + timedelta(seconds=60)
    # engine: stop_at never moves backwards and never past the target
    early = parse_ts(w.ctl.run_info(w.run)["virtual_time"]) - timedelta(seconds=5)
    same = w.ctl.advance(w.run, 30, stop_at=early)
    assert parse_ts(same["virtual_time"]) == early + timedelta(seconds=5)
    late = w.ctl.advance(w.run, 30, stop_at=early + timedelta(days=1))
    assert parse_ts(late["virtual_time"]) == early + timedelta(seconds=35)


# ------------------------------------------------------ checks off: idle is undecided
def test_idle_agent_with_checks_off_is_inconclusive_without_accuracy(env, tmp_path):
    ctl = Controller(env.engine, env.scope)
    ev = EvaluatorView(env.engine, env.scope)
    run = ctl.create_run(env.version(write_variant(tmp_path, open_all)), checks="off")
    ctl.start_run(run)
    w = W.__new__(W)
    w.env, w.ctl, w.ev, w.run = env, ctl, ev, run
    v = w.key(
        5,
        {"id": "replied", "type": "action", "requirement": "r", "check": {"operation": "reply"}},
        {"id": "no-ext", "type": "invariant", "requirement": "r",
         "check": {"invariant": "no_external_delivery_attempts"}},
    )  # fmt: skip
    rep = evaluate_run(ev, run, answer_key_version=v, evaluation_id="e")
    assert [o.status.value for o in rep.outcomes] == ["NOT_APPLICABLE", "PASS"]
    assert rep.verdict.value == "INCONCLUSIVE"
    assert rep.verdict_reasons == ["no mandatory grading assertion was decided"]
    assert rep.accuracy.overall is None and rep.accuracy.decided == 0
    on = evaluate_run(ev, run, answer_key_version=v, checks="on", evaluation_id="e2")
    assert on.verdict.value == "FAILED" and on.accuracy.overall == 0.5
