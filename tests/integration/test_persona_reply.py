"""Controller.persona_reply: persona-authored mail is delivered AND recorded for replay."""

import pytest

from mailsim.domain.errors import ExternalDeliveryBlocked, InvalidRequest, NotFound
from mailsim.engine.facades import Controller, EvaluatorView
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
GEN = {"mode": "llm", "provider": "stub", "model": "stub-1", "prompt_hash": "abc",
       "input_tokens": 10, "output_tokens": 5}  # fmt: skip


def setup(env, tmp_path, mode="deny"):
    def tweak(s):
        s["personas"].append({"id": "cfo", "email": "cfo@example.test", "role": "CFO"})
        s["environment"]["delivery"]["external_delivery"] = mode

    ctl = Controller(env.engine, env.scope)
    run = ctl.create_run(env.version(write_variant(tmp_path, tweak)))
    ctl.start_run(run)
    return ctl, EvaluatorView(env.engine, env.scope), run


def test_persona_with_mailbox_replies_in_thread(env, tmp_path):
    ctl, ev, run = setup(env, tmp_path)
    fm = ctl.mailbox_id_for(run, "finance_manager")
    invoice = next(m for m in ev.state(run)["messages"] if m["subject"] == "Invoice INV-001")
    res = ctl.persona_reply(run, "vendor", to=["finance@example.test"], subject="Re: Invoice INV-001",
                            text="Payment due Friday.", generation=GEN,
                            in_reply_to_message_id=invoice["id"])  # fmt: skip
    msgs = {m["id"]: m for m in ev.state(run)["messages"]}
    delivered = msgs[res["delivered_message_ids"][0]]
    assert (
        delivered["mailbox_id"] == fm and delivered["in_reply_to"] == invoice["message_id_header"]
    )
    assert delivered["thread_id"] == invoice["thread_id"]
    assert msgs[res["message_id"]]["folder_id"] != delivered["folder_id"], (
        "vendor keeps a Sent copy"
    )
    rec = [e for e in ev.events(run) if e["type"] == "persona.generated"][0]
    assert rec["payload"]["text"] == "Payment due Friday." and rec["payload"]["generation"] == GEN
    assert ctl.replay_run(run).matches, "replay consumes the recording, never a model"


def test_persona_without_mailbox_and_validation(env, tmp_path):
    ctl, ev, run = setup(env, tmp_path)
    res = ctl.persona_reply(run, "cfo", to=["finance@example.test"], subject="Approval",
                            text="Approved.", generation={"mode": "scripted", "rule_index": 0})  # fmt: skip
    assert len(res["delivered_message_ids"]) == 1 and res["external_recipients"] == []
    with pytest.raises(InvalidRequest, match="mode"):
        ctl.persona_reply(
            run, "cfo", to=["finance@example.test"], subject="x", text="y", generation={}
        )
    with pytest.raises(InvalidRequest, match="unknown generation fields"):
        ctl.persona_reply(run, "cfo", to=["finance@example.test"], subject="x", text="y",
                          generation={"mode": "llm", "secret_prompt": "..."})  # fmt: skip
    with pytest.raises(NotFound):
        ctl.persona_reply(
            run, "ghost", to=["finance@example.test"], subject="x", text="y", generation=GEN
        )
    with pytest.raises(NotFound):
        ctl.persona_reply(run, "cfo", to=["finance@example.test"], subject="x", text="y",
                          generation=GEN, in_reply_to_message_id="msg_nope")  # fmt: skip
    with pytest.raises(InvalidRequest):
        ctl.persona_reply(run, "cfo", to=[], subject="x", text="y", generation=GEN)
    with pytest.raises(ExternalDeliveryBlocked):
        ctl.persona_reply(run, "cfo", to=["x@real.example"], subject="x", text="y", generation=GEN)
    assert ctl.replay_run(run).matches


def test_persona_external_recipient_in_event_mode(env, tmp_path):
    ctl, ev, run = setup(env, tmp_path, mode="event")
    res = ctl.persona_reply(run, "cfo", to=["finance@example.test", "x@real.example"], subject="s",
                            text="t", generation=GEN)  # fmt: skip
    assert res["external_recipients"] == ["x@real.example"]
    assert any(e["type"] == "delivery.external_attempt" for e in ev.events(run))


def test_agent_owner_and_persona_generations(env, tmp_path):
    ctl, ev, run = setup(env, tmp_path)
    assert ctl.agent_mailbox_owner(run) == "finance_manager"
    assert ctl.persona_generations(run) == []
    ctl.persona_reply(
        run, "cfo", to=["finance@example.test"], subject="s", text="t", generation=GEN
    )
    gens = ctl.persona_generations(run)
    assert [g["persona_id"] for g in gens] == ["cfo"] and gens[0]["sequence"] > 0
    ctl.reset_run(run)
    assert ctl.persona_generations(run) == [], "current epoch only"
    acts = ctl.actions(run)
    assert all("id" in x and "virtual_time" in x for x in acts)
    assert [x["id"] for x in acts] == sorted(x["id"] for x in acts)
