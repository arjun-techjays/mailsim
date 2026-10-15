"""Persona runtime against the real engine (PostgreSQL) through the Controller facade."""

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from mailsim.domain.errors import InvalidRequest, SimulationLimitExceeded
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.llm.contract import Budget
from mailsim.llm.gateway import LLMGateway, LLMProviderError
from mailsim.llm.providers.stub import StubProvider
from mailsim.personas.runtime import PersonaRuntime, reply_subject
from mailsim.store import models as m
from tests.llm.conftest import ScriptedProvider
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
SNAPSHOTS = Path(__file__).parent / "snapshots"
FORBIDDEN_IN_PROMPTS = [
    "Invoices above INR 200000",  # organisation policy
    "Bank changes require independent verification",  # organisation policy
    "invoice_policy", "approved_vendor_records",  # finance_manager's knowledge
    "Agent must not claim payment",  # answer key / evaluation
    "Bank-detail discrepancy", "No approval is recorded",
    "cfo_ledger",  # another persona's knowledge
]  # fmt: skip

VENDOR_SCRIPT = [
    {"on": {"subject_contains": "INVOICE", "body_contains": "payment"},
     "reply": {"body": "Payment terms are 30 days."}, "delay_seconds": 60, "once": True},
    {"on": {"from": "finance_manager", "subject_contains": "invoice"},
     "reply": {"subject": "Invoice follow-up", "body": "Noted, thanks."}, "once": False},
]  # fmt: skip


def scenario(tmp_path, *, vendor=None, cfo=None, fm=None, events=None, extra_personas=()):
    def tweak(s):
        s["agent"]["allowed_operations"] = ["read", "search", "reply", "move", "send"]
        by_id = {p["id"]: p for p in s["personas"]}
        if fm is not None:
            by_id["finance_manager"]["behavior"].update(fm)
        by_id["vendor"]["behavior"].update(
            vendor or {"mode": "scripted", "response_delay_seconds": 5, "script": VENDOR_SCRIPT}
        )
        s["personas"].append({"id": "cfo", "email": "cfo@example.test", "role": "CFO",
                              "knowledge": ["cfo_ledger"],
                              "behavior": cfo or {"mode": "silent"}})  # fmt: skip
        s["personas"].extend(extra_personas)
        s["mailboxes"].append({"owner": "cfo"})
        s["events"] = events or []

    return write_variant(tmp_path, tweak)


class Harness:
    def __init__(self, env, tmp_path, provider=None, budget=None, **kw):
        self.ctl = Controller(env.engine, env.scope)
        self.ev = EvaluatorView(env.engine, env.scope)
        self.run = self.ctl.create_run(env.version(scenario(tmp_path, **kw)))
        self.ctl.start_run(self.run)
        self.provider = provider or ScriptedProvider("Happy to help with that.")
        self.gateway = LLMGateway(self.provider, session_factory=env.session_factory,
                                  budget=budget)  # fmt: skip
        self.rt = PersonaRuntime(self.ctl, self.gateway, agent_owner=lambda run: "finance_manager")
        self.agent = AgentMailbox(env.engine, env.scope, self.run,
                                  self.ctl.mailbox_id_for(self.run, "finance_manager"))  # fmt: skip
        self.sf = env.session_factory

    def send(self, to, subject, text):
        return self.agent.send(to=[to], subject=subject, text=text)

    def generated(self):
        return [e["payload"] for e in self.ev.events(self.run) if e["type"] == "persona.generated"]

    def inbox(self, persona="finance_manager"):
        mbx = self.ctl.mailbox_id_for(self.run, persona)
        return [x for x in self.ctl.state(self.run)["messages"] if x["mailbox_id"] == mbx]


def test_scripted_match_delay_once_and_idempotency(env, tmp_path):
    h = Harness(env, tmp_path)
    sent = h.send("vendor@example.test", "Invoice INV-001 question", "When is PAYMENT due?")
    assert h.rt.step(h.run) == [], "rule 0 has delay_seconds=60"
    h.ctl.advance(h.run, 59)
    assert h.rt.step(h.run) == []
    h.ctl.advance(h.run, 1)
    [done] = h.rt.step(h.run)
    assert done["mode"] == "scripted" and done["subject"] == "Re: Invoice INV-001 question"
    assert h.rt.step(h.run) == [], "idempotent: already answered"
    reply = next(x for x in h.inbox() if x["subject"] == "Re: Invoice INV-001 question")
    assert reply["body_text"].strip() == "Payment terms are 30 days."
    sent_hdr = next(x for x in h.inbox("vendor") if x["id"] != reply["id"]
                    and x["subject"] == "Invoice INV-001 question")["message_id_header"]  # fmt: skip
    assert reply["in_reply_to"] == sent_hdr and reply["from"]["email"] == "vendor@example.test"
    assert sent["message_id"]
    assert h.generated()[0]["generation"] == {"mode": "scripted", "rule_index": 0}
    # rule 0 is once: the next matching mail falls through to rule 1 (no delay -> 5s default)
    h.send("vendor@example.test", "Invoice INV-002", "payment please")
    assert h.rt.step(h.run) == []
    h.ctl.advance(h.run, 5)
    [second] = h.rt.step(h.run)
    assert second["subject"] == "Invoice follow-up"
    assert h.generated()[1]["generation"] == {"mode": "scripted", "rule_index": 1}
    # no rule matches -> never answered
    h.send("vendor@example.test", "Lunch?", "Are you free?")
    h.ctl.advance(h.run, 600)
    assert h.rt.step(h.run) == []
    assert h.provider.calls == 0, "scripted personas never call a model"
    assert h.ctl.replay_run(h.run).matches


def test_scripted_from_filter_and_other_senders(env, tmp_path):
    script = [{"on": {"from": "cfo"}, "reply": {"body": "Hi CFO."}}]
    events = [{"id": "fm-to-vendor", "trigger": {"type": "after_seconds", "seconds": 10},
               "event": {"type": "email.receive", "from": "cfo", "to": "vendor",
                         "subject": "Budget", "body": "Numbers?"}}]  # fmt: skip
    h = Harness(env, tmp_path, vendor={"mode": "scripted", "script": script}, events=events)
    h.send("vendor@example.test", "Invoice", "payment")
    assert h.rt.step(h.run) == [], "agent mail does not match on.from=cfo"
    h.ctl.advance(h.run, 10)
    assert h.rt.step(h.run) == [], "scenario response_delay_seconds (15) applies"
    h.ctl.advance(h.run, 15)
    [done] = h.rt.step(h.run)
    assert done["result"]["delivered_message_ids"]
    cfo_inbox = h.inbox("cfo")
    assert any(x["subject"] == "Re: Budget" and "Hi CFO." in x["body_text"] for x in cfo_inbox)


def test_llm_persona_reply_is_recorded_and_replay_makes_zero_calls(env, tmp_path):
    h = Harness(env, tmp_path, cfo={"mode": "llm", "response_style": "terse",
                                     "response_delay_seconds": 30})  # fmt: skip
    h.send("cfo@example.test", "Approval for INV-001", "Can you approve the invoice?")
    assert h.rt.step(h.run) == [] and h.provider.calls == 0
    h.ctl.advance(h.run, 30)
    [done] = h.rt.step(h.run)
    assert done["mode"] == "llm" and h.provider.calls == 1
    gen = h.generated()[0]
    assert gen["text"] == "Happy to help with that."
    g = gen["generation"]
    assert set(g) == {"mode", "provider", "model", "prompt_hash", "input_tokens",
                      "output_tokens", "llm_call_id"}  # fmt: skip
    assert (g["mode"], g["provider"], g["model"], g["input_tokens"], g["output_tokens"]) == (
        "llm", "stub", "fake-1", 10, 5,
    )  # fmt: skip
    with h.sf() as s:
        row = s.get(m.LlmCall, g["llm_call_id"])
        assert (
            row.run_id == h.run and row.purpose == "persona" and row.prompt_hash == g["prompt_hash"]
        )
    assert h.rt.step(h.run) == [] and h.provider.calls == 1, "idempotent"
    before = h.provider.calls
    result = h.ctl.replay_run(h.run)
    assert result.matches and h.provider.calls == before, "replay never calls a model"
    with h.sf() as s:
        n = s.execute(select(func.count(m.LlmCall.id)).where(m.LlmCall.run_id == h.run)).scalar()
        assert n == 1
    replayed = [e for e in h.ev.events(result.replay_run_id) if e["type"] == "persona.generated"]
    assert replayed[0]["payload"]["text"] == "Happy to help with that."


def test_vendor_prompt_is_knowledge_isolated_snapshot(env, tmp_path):
    h = Harness(env, tmp_path, vendor={"mode": "llm", "response_style": "professional",
                                        "response_delay_seconds": 0},
                cfo={"mode": "llm"})  # fmt: skip
    h.send("vendor@example.test", "Bank details", "Please confirm your bank account for INV-001.")
    [done] = h.rt.step(h.run)
    [request] = h.provider.requests
    prompt = f"SYSTEM:\n{request.system}\n\nUSER:\n{request.messages[0].content}\n"
    for text in FORBIDDEN_IN_PROMPTS:
        assert text not in prompt, text
    assert "invoice_001" in prompt and "Vendor" in prompt and "professional" in prompt
    snap = SNAPSHOTS / "vendor_prompt.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):  # pragma: no cover - maintenance only
        snap.write_text(prompt)
    assert prompt == snap.read_text(), "persona prompt changed: review and update the snapshot"
    # a follow-up in the same thread carries the persona's own earlier reply, still isolated
    h.agent.reply(h.inbox()[-1]["id"], text="Thanks, what is the IBAN?")
    h.rt.step(h.run)
    follow_up = h.provider.requests[1].messages[0].content
    assert "--- your message ---" in follow_up and "Happy to help with that." in follow_up
    assert not any(t in follow_up for t in FORBIDDEN_IN_PROMPTS)
    assert done["persona_id"] == "vendor"


def test_silent_agent_and_mailboxless_personas_never_reply(env, tmp_path):
    ghost = {"id": "auditor", "email": "auditor@example.test", "role": "Auditor",
             "behavior": {"mode": "llm"}}  # fmt: skip
    h = Harness(env, tmp_path, vendor={"mode": "silent"}, fm={"mode": "llm"},
                extra_personas=[ghost])  # fmt: skip
    h.send("vendor@example.test", "Invoice", "payment")
    h.ctl.inject(h.run, h.ctl.mailbox_id_for(h.run, "finance_manager"),
                 {"from": "cfo@example.test", "to": ["finance@example.test"],
                  "subject": "hi", "text": "x"})  # fmt: skip
    h.ctl.advance(h.run, 3600)
    assert h.rt.step(h.run) == [] and h.provider.calls == 0


def test_outsider_mail_is_never_answered(env, tmp_path):
    h = Harness(env, tmp_path, cfo={"mode": "llm", "response_delay_seconds": 0})
    h.ctl.inject(h.run, h.ctl.mailbox_id_for(h.run, "cfo"),
                 {"from": "stranger@real.example", "to": ["cfo@example.test"],
                  "subject": "hi", "text": "x"})  # fmt: skip
    assert h.rt.step(h.run) == [] and h.provider.calls == 0


def test_llm_personas_do_not_ping_pong(env, tmp_path):
    events = [{"id": "v2c", "trigger": {"type": "after_seconds", "seconds": 1},
               "event": {"type": "email.receive", "from": "vendor", "to": "cfo",
                         "subject": "Hello", "body": "Hi"}}]  # fmt: skip
    h = Harness(env, tmp_path, vendor={"mode": "llm"}, cfo={"mode": "llm"}, events=events)
    h.ctl.advance(h.run, 5)
    assert h.rt.step(h.run) == [] and h.provider.calls == 0


def test_inactive_run_and_missing_agent_owner(env, tmp_path):
    h = Harness(env, tmp_path)
    h.ctl.cancel_run(h.run)
    assert h.rt.step(h.run) == []
    run2 = h.ctl.create_run(env.version(scenario(tmp_path)))
    assert h.rt.step(run2) == [], "not started"
    h.ctl.start_run(run2)
    with pytest.raises(InvalidRequest, match="agent mailbox owner"):
        PersonaRuntime(h.ctl, h.gateway).step(run2)

    class ControllerWithOwner(Controller):
        def agent_mailbox_owner(self, run_id):
            return "finance_manager"

    rt = PersonaRuntime(ControllerWithOwner(env.engine, env.scope), h.gateway)
    assert rt.agent_owner(run2) == "finance_manager" and rt.step(run2) == []


def test_llm_errors_and_budget_propagate(env, tmp_path):
    h = Harness(env, tmp_path, provider=ScriptedProvider("   "), cfo={"mode": "llm"})
    h.send("cfo@example.test", "Q", "?")
    with pytest.raises(LLMProviderError, match="empty"):
        h.rt.step(h.run)
    assert h.generated() == []
    h2 = Harness(env, tmp_path, cfo={"mode": "llm"}, budget=Budget(max_calls=0))
    h2.send("cfo@example.test", "Q", "?")
    with pytest.raises(SimulationLimitExceeded):
        h2.rt.step(h2.run)
    assert h2.provider.calls == 0


def test_stub_provider_end_to_end(env, tmp_path):
    h = Harness(env, tmp_path, provider=StubProvider(), cfo={"mode": "llm"})
    h.send("cfo@example.test", "Re: Approval", "<b>ok?</b>")
    [done] = h.rt.step(h.run)
    assert done["subject"] == "Re: Approval"
    assert h.generated()[0]["generation"]["provider"] == "stub"


def test_reply_subject():
    assert reply_subject("Invoice") == "Re: Invoice"
    assert reply_subject("RE: Invoice") == "RE: Invoice"
    assert reply_subject("") == "Re:"
