"""W2.1 facade additions: reply overrides (one `reply` operation) and mailbox_info."""

import pytest

from mailsim.domain.errors import ExternalDeliveryBlocked, InvalidRequest, PermissionDenied
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView

pytestmark = pytest.mark.db


@pytest.fixture()
def finance(env):
    """The shipped scenario: allowed_operations [read, search, reply, move] + a send_reply trigger."""
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    return (
        env,
        run,
        AgentMailbox(env.engine, env.scope, run, fm),
        EvaluatorView(env.engine, env.scope),
    )


def test_reply_overrides_are_one_reply_operation(finance):
    env, run, agent, ev = finance
    first = agent.list_messages(folder="inbox").items[0]
    res = agent.reply(first.id, text="Escalating.", to=["vendor@example.test"],
                      cc=["finance@example.test"], bcc=["Vendor@example.test"],
                      subject="AW: custom subject", html="<p>Escalating.</p>")  # fmt: skip
    sent = agent.get_message(res["message_id"])
    assert sent.subject == "AW: custom subject" and sent.body_html.strip() == "<p>Escalating.</p>"
    assert [a.email for a in sent.to] == ["vendor@example.test"]
    assert [a.email for a in sent.cc] == ["finance@example.test"]
    assert sent.in_reply_to == first.message_id_header
    assert first.message_id_header in sent.references
    assert "ANSWERED" in agent.get_message(first.id).flags
    actions = [
        a
        for a in ev.actions(run)
        if a["actor"] == "agent" and a["operation"] not in ("read", "search")
    ]
    assert [a["operation"] for a in actions] == ["reply"]  # not send (forbidden here) or draft
    assert actions[0]["input"]["overrides"] == ["bcc", "cc", "html", "subject", "to"]
    assert any(e["type"] == "event.armed" for e in ev.events(run)), "send_reply trigger arms"
    with pytest.raises(PermissionDenied):  # sending is still not allowed in this scenario
        agent.send(to=["vendor@example.test"], subject="x")
    assert Controller(env.engine, env.scope).replay_run(run).matches


def test_reply_override_validation_and_delivery_policy(finance):
    env, run, agent, ev = finance
    first = agent.list_messages(folder="inbox").items[0]
    with pytest.raises(InvalidRequest):
        agent.reply(first.id, text="x", to=[], cc=[])
    with pytest.raises(ExternalDeliveryBlocked):
        agent.reply(first.id, text="x", cc=["someone@outside.invalid"])
    assert "ANSWERED" not in agent.get_message(first.id).flags
    assert agent.list_messages(folder="sent").items == []
    plain = agent.reply(first.id, text="ok", subject=None)  # defaults unchanged
    assert agent.get_message(plain["message_id"]).subject.lower().startswith("re:")


def test_mailbox_info(finance):
    env, run, agent, ev = finance
    info = agent.mailbox_info()
    assert info["mailbox_id"] == agent.mailbox_id and info["run_id"] == run
    assert info["email"].endswith("@example.test") and info["virtual_time"]


def test_w22_reads_get_raw_find_by_message_id_and_mark_seen(finance):
    env, run, agent, ev = finance
    first = agent.list_messages(folder="inbox").items[0]
    raw = agent.get_raw(first.id)
    assert raw is not None and first.message_id_header.encode() in raw
    found = agent.find_by_message_id(first.message_id_header)
    assert [m.id for m in found] == [first.id]
    assert agent.find_by_message_id("<nope@example.test>") == []
    before = Controller(env.engine, env.scope).run_info(run)["agent_actions"]
    assert "SEEN" not in first.flags
    assert agent.mark_seen(first.id) is True
    assert agent.mark_seen(first.id) is False  # already seen: no event
    assert "SEEN" in agent.get_message(first.id).flags
    assert Controller(env.engine, env.scope).run_info(run)["agent_actions"] == before  # reads only
    seen = [e for e in ev.events(run) if e["type"] == "message.flags_changed"]
    assert len(seen) == 1 and seen[0]["operation"] == "read"
    assert Controller(env.engine, env.scope).replay_run(run).matches


def test_get_raw_without_a_stored_blob(finance, monkeypatch):
    env, run, agent, ev = finance
    first = agent.list_messages(folder="inbox").items[0]
    from sqlalchemy import update

    from mailsim.store import models as m

    with env.session_factory.begin() as s:
        s.execute(update(m.Message).where(m.Message.id == first.id).values(raw_blob_ref=None))
    assert agent.get_raw(first.id) is None
