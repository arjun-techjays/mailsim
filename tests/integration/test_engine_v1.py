"""Engine command interface v1 (Wave 0): role facades, new commands, pagination, changes."""

import pytest

from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import (
    Conflict,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    RunNotActive,
)
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView, facade_for
from mailsim.engine.pagination import decode_cursor, encode_cursor
from tests.helpers import PDF, attachment_part, multipart, text_part
from tests.scenarios import write_variant

pytestmark = pytest.mark.db


def open_all(s):
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 200


@pytest.fixture()
def world(env, tmp_path):
    ctl = Controller(env.engine, env.scope)
    run = ctl.create_run(env.version(write_variant(tmp_path, open_all)), agent_build="b1")
    ctl.start_run(run)
    fm = ctl.mailbox_id_for(run, "finance_manager")
    vendor = ctl.mailbox_id_for(run, "vendor")
    agent = AgentMailbox(env.engine, env.scope, run, fm)
    ev = EvaluatorView(env.engine, env.scope)
    vend = AgentMailbox(env.engine, env.scope, run, vendor)  # agent facade on the wrong mailbox
    return env, ctl, ev, run, agent, fm, vendor, vend


def vendor_inbox(env, run, vendor):
    from mailsim.engine.core import CONTROLLER

    return env.engine.list_messages(env.scope, run, vendor, folder="inbox", actor=CONTROLLER)


def test_facade_for_roles(env):
    p = Principal(env.scope.tenant_id, env.scope.project_id, Role.AGENT, "run_x", "mbx_x")
    assert isinstance(facade_for(env.engine, p), AgentMailbox)
    assert isinstance(
        facade_for(
            env.engine, Principal(env.scope.tenant_id, env.scope.project_id, Role.CONTROLLER)
        ),
        Controller,
    )
    assert isinstance(
        facade_for(
            env.engine, Principal(env.scope.tenant_id, env.scope.project_id, Role.EVALUATOR)
        ),
        EvaluatorView,
    )
    with pytest.raises(PermissionDenied):
        facade_for(env.engine, Principal(env.scope.tenant_id, env.scope.project_id, Role.AGENT))


def test_agent_facade_has_no_control_or_evaluation_surface():
    public = {n for n in dir(AgentMailbox) if not n.startswith("_")}
    forbidden = {"reset_run", "advance", "tick", "inject", "state", "events", "actions",
                 "scenario_spec", "complete_run", "replay_run", "create_run", "cancel_run"}  # fmt: skip
    assert public.isdisjoint(forbidden)
    assert "complete_run" not in {n for n in dir(Controller) if not n.startswith("_")}


def test_agent_facade_cannot_escape_its_mailbox(world):
    env, ctl, ev, run, agent, fm, vendor, vend = world
    with pytest.raises(PermissionDenied):
        vend.list_messages()  # an agent facade bound to someone else's mailbox is refused
    assert ctl.run_info(run)["agent_actions"] == 0


def test_pagination_is_complete_and_stable(world):
    env, ctl, ev, run, agent, *_ = world
    for i in range(7):
        ctl.inject(run, agent.mailbox_id, {"from": "vendor@example.test",
                                           "to": ["finance@example.test"], "subject": f"p{i}", "text": "t"})  # fmt: skip
    seen, cursor = [], None
    while True:
        page = agent.list_messages(page_size=3, cursor=cursor)
        seen += [m.id for m in page.items]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert len(seen) == 9 == len(set(seen))
    assert seen == [m.id for m in agent.list_messages(page_size=500).items]
    assert [m.id for m in agent.list_messages(folder="inbox", page_size=2).items][:2] == seen[:2]
    for bad in ("garbage!!", encode_cursor("x", "y")[:-2] + "!!", "WzFd"):
        with pytest.raises(InvalidRequest):
            agent.list_messages(cursor=bad)
    with pytest.raises(InvalidRequest):
        agent.list_messages(page_size=0)
    assert decode_cursor(encode_cursor("a", "b")) == ("a", "b")


def test_change_feed_and_reset_flag(world):
    env, ctl, ev, run, agent, *_ = world
    start = agent.list_changes(0)
    assert start["changes"] and start["latest_sequence"] > 0 and start["reset_since"] is False
    mark = start["latest_sequence"]
    first = agent.list_messages().items[0]
    agent.set_flags(first.id, add=["SEEN"])
    feed = agent.list_changes(mark)
    assert [(c["type"], c["message_id"]) for c in feed["changes"]] == [
        ("message.flags_changed", first.id)
    ]
    with pytest.raises(InvalidRequest):
        agent.list_changes(-1)
    ctl.reset_run(run)
    ctl.start_run(run)
    after = agent.list_changes(mark)
    assert after["reset_since"] is True


def test_forward_carries_attachments(world):
    env, ctl, ev, run, agent, fm, vendor, _ = world
    invoice = next(m for m in agent.list_messages().items if m.attachments)
    res = agent.forward(invoice.id, to=["vendor@example.test"], text="FYI")
    got = vendor_inbox(env, run, vendor)
    assert (
        got[0].subject == "Fwd: Invoice INV-001" and got[0].attachments[0].filename == "INV-001.pdf"
    )
    assert "Forwarded message" in got[0].body_text and res["external_recipients"] == []
    again = agent.forward(agent.get_message(res["message_id"]).id, to=["vendor@example.test"])
    assert agent.get_message(again["message_id"]).subject == "Fwd: Invoice INV-001"


def test_insert_does_not_deliver(world):
    env, ctl, ev, run, agent, fm, vendor, _ = world
    raw = multipart([text_part("inserted"), attachment_part("a.pdf", "application/pdf", PDF)])
    res = agent.insert_message(raw.replace(b"b@example.test", b"vendor@example.test"))
    msg = agent.get_message(res["message_id"])
    assert msg.folder == "INBOX" and msg.attachments
    assert vendor_inbox(env, run, vendor) == []
    archived = agent.insert_message(raw.replace(b"<mp@", b"<mp2@"), folder="archive")
    assert agent.get_message(archived["message_id"]).folder == "Archive"


def test_drafts_update_and_send(world):
    env, ctl, ev, run, agent, fm, vendor, _ = world
    d = agent.create_draft(to=["vendor@example.test"], subject="draft", text="v1")
    u = agent.update_draft(
        d["message_id"], to=["vendor@example.test"], subject="draft v2", text="v2"
    )
    assert u["draft_id"] == d["draft_id"] and u["message_id"] != d["message_id"]
    with pytest.raises(NotFound):
        agent.get_message(d["message_id"])
    sent = agent.send_draft(u["message_id"])
    assert agent.list_messages(folder="drafts").items == []
    assert agent.get_message(sent["message_id"]).folder == "Sent"
    assert vendor_inbox(env, run, vendor)[0].subject == "draft v2"
    inbox_msg = agent.list_messages(folder="inbox").items[0]
    with pytest.raises(Conflict):
        agent.update_draft(inbox_msg.id, to=["vendor@example.test"], subject="x")
    with pytest.raises(Conflict):
        agent.send_draft(inbox_msg.id)
    assert ctl.replay_run(run).matches


def test_copy_trash_untrash_expunge(world):
    env, ctl, ev, run, agent, *_ = world
    first = agent.list_messages(folder="inbox").items[0]
    copied = agent.copy(first.id, "archive")
    c = agent.get_message(copied["message_id"])
    assert c.folder == "Archive" and c.message_id_header == first.message_id_header
    assert c.thread_id == first.thread_id and len(c.attachments) == len(first.attachments)
    agent.move(first.id, "archive")
    agent.trash(first.id)
    agent.trash(first.id)  # idempotent
    assert agent.get_message(first.id).folder == "Trash"
    agent.untrash(first.id)
    assert agent.get_message(first.id).folder == "Archive", "restored to where it came from"
    agent.untrash(first.id)  # no-op when not trashed
    agent.set_flags(c.id, add=["DELETED"])
    assert agent.expunge("archive") == [c.id]
    assert agent.expunge("archive") == []
    assert [f["system_type"] for f in agent.list_folders()][0] == "inbox"
    assert agent.search_messages("purchase order")
    assert len(agent.get_thread(first.thread_id)) >= 2
    agent.modify_labels(first.id, add=["x"])
    agent.reply(first.id, text="ok")
    agent.send(to=["vendor@example.test"], subject="s")
    agent.delete(first.id)
    assert ctl.replay_run(run).matches


def test_untrash_without_history_goes_to_inbox(world):
    env, ctl, ev, run, agent, *_ = world
    msg = agent.list_messages(folder="inbox").items[0]
    agent.move(msg.id, "trash")
    agent.untrash(msg.id)
    assert agent.get_message(msg.id).folder == "INBOX"


def test_evaluator_view_and_complete_run(world):
    env, ctl, ev, run, agent, *_ = world
    spec = ev.scenario_spec(run)
    assert spec["spec"]["scenario"]["id"] == "finance.invoice-bank-change"
    assert ev.state(run)["messages"] and ev.events(run) and ev.actions(run)
    assert len(ev.state_hash(run)) == 64
    assert ctl.run_info(run)["status"] == "RUNNING" and ctl.actions(run)
    with pytest.raises(InvalidRequest):
        ev.complete_run(run, "MAYBE", {})
    ev.complete_run(run, "PASSED", {"accuracy": 1.0})
    assert ev.run_info(run)["status"] == "PASSED"
    assert ev.events(run)[-1]["type"] in ("run.completed", "ids.reserved")
    with pytest.raises(RunNotActive):
        ev.complete_run(run, "FAILED", {})
    with pytest.raises(Conflict):
        ctl.reset_run(run)


def test_controller_surface(world):
    env, ctl, ev, run, agent, fm, *_ = world
    assert ctl.tick(run)["fired"] == []
    assert ctl.advance(run, 600)["fired"] == ["vendor-follow-up"]
    ctl.fail_run_with_error(run, "x")
    other = ctl.create_run(ev.scenario_spec(run)["scenario_version_id"])
    ctl.cancel_run(other)
    assert ctl.run_info(other)["status"] == "CANCELLED"


def test_run_level_checks_switch_and_build_are_validated(env):
    ctl = Controller(env.engine, env.scope)
    version = env.version()
    with pytest.raises(InvalidRequest):
        ctl.create_run(version, checks="maybe")
    with pytest.raises(InvalidRequest):
        ctl.create_run(version, agent_build="")
    run = ctl.create_run(version, checks="off", agent_build="build-42")
    created = EvaluatorView(env.engine, env.scope).events(run)[0]["payload"]["config"]["evaluation"]
    assert created == {"checks": "off", "agent_build": "build-42"}
