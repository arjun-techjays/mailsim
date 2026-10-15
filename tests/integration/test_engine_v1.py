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
    assert ctl.state(run) == ev.state(run)
    assert len(ev.state_hash(run)) == 64
    assert ctl.run_info(run)["status"] == "RUNNING" and ctl.actions(run)
    seq = ev.events(run)[-1]["sequence"]
    with pytest.raises(InvalidRequest):
        ev.complete_run(run, "MAYBE", evaluation_id="ev_1", evaluated_through_sequence=seq)
    agent.set_flags(agent.list_messages().items[0].id, add=["SEEN"])  # agent acts after snapshot
    with pytest.raises(Conflict, match="changed after it was evaluated"):
        ev.complete_run(run, "PASSED", evaluation_id="ev_1", evaluated_through_sequence=seq)
    seq = ev.events(run)[-1]["sequence"]  # ... so re-evaluate, then complete
    ev.complete_run(run, "PASSED", evaluation_id="ev_2", evaluated_through_sequence=seq)
    assert ev.run_info(run)["status"] == "PASSED"
    last = ev.events(run)[-1]
    assert last["type"] == "run.completed"
    assert last["payload"] == {"verdict": "PASSED", "evaluation_id": "ev_2"}
    with pytest.raises(RunNotActive):
        ev.complete_run(run, "FAILED", evaluation_id="ev_3",
                        evaluated_through_sequence=last["sequence"])  # fmt: skip
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


def test_change_feed_pages_without_losing_changes(world):
    env, ctl, ev, run, agent, *_ = world
    start = agent.list_changes(0)["latest_sequence"]
    msgs = agent.list_messages().items
    for m in msgs:
        agent.set_flags(m.id, add=["SEEN"])
        agent.modify_labels(m.id, add=["x"])
    seen, since = [], start
    while True:
        page = agent.list_changes(since, limit=1)
        seen += [c["sequence"] for c in page["changes"]]
        since = page["next_since"]
        if not page["has_more"]:
            break
    assert len(seen) == 2 * len(msgs) == len(set(seen)), "no change skipped when paging"


def test_denied_agent_action_is_logged_with_its_error_code(world):
    env, ctl, ev, run, agent, *_ = world
    with pytest.raises(NotFound):
        agent.move("msg_nope", "archive")
    counted = [e for e in ev.events(run) if e["type"] == "agent.action_counted"][-1]
    assert counted["outcome"] == "NOT_FOUND" and counted["payload"]["outcome"] == "NOT_FOUND"


def test_external_ids_imap_uids_drafts_and_order(world):
    env, ctl, ev, run, agent, *_ = world
    m0 = agent.list_messages().items[0]
    assert m0.imap_uid == 1
    assert m0.provider_ids["gmail_message_id"] and m0.provider_ids["graph_thread_id"]
    assert (
        agent.resolve_external_id("gmail", "message", m0.provider_ids["gmail_message_id"]) == m0.id
    )
    assert (
        agent.resolve_external_id("graph", "thread", m0.provider_ids["graph_thread_id"])
        == m0.thread_id
    )
    with pytest.raises(NotFound):
        agent.resolve_external_id("gmail", "message", "0" * 16)
    with pytest.raises(InvalidRequest):
        agent.resolve_external_id("gmail", "label", "x")
    d = agent.create_draft(to=["vendor@example.test"], subject="d", cc=["finance@example.test"],
                           bcc=["vendor@example.test"], html="<b>x</b>",
                           in_reply_to=m0.message_id_header, references=[m0.message_id_header])  # fmt: skip
    draft = agent.get_message(d["message_id"])
    assert draft.provider_ids["draft_id"] == d["draft_id"] and draft.thread_id == m0.thread_id
    assert [a.email for a in draft.bcc] == ["vendor@example.test"]
    gmail_draft = draft.provider_ids["gmail_draft_id"]
    assert agent.resolve_external_id("gmail", "draft", gmail_draft) == d["message_id"]
    u = agent.update_draft(d["message_id"], to=["vendor@example.test"], subject="d2",
                           cc=["finance@example.test"])  # fmt: skip
    updated = agent.get_message(u["message_id"])
    assert updated.in_reply_to == m0.message_id_header and updated.thread_id == m0.thread_id
    assert [a.email for a in updated.cc] == ["finance@example.test"]
    assert agent.resolve_external_id("gmail", "draft", gmail_draft) == u["message_id"]
    folders = {f["system_type"]: f for f in agent.list_folders()}
    assert folders["inbox"]["uid_validity"] == 1 and folders["inbox"]["uid_next"] == 3
    asc_ids = [m.id for m in agent.list_messages(page_size=500).items]
    desc = agent.list_messages(order="desc", page_size=2)
    rest = agent.list_messages(order="desc", page_size=500, cursor=desc.next_cursor).items
    assert [m.id for m in desc.items + rest] == list(reversed(asc_ids))
    with pytest.raises(InvalidRequest):
        agent.list_messages(order="sideways")


def test_external_ids_never_resolve_across_mailboxes(world):
    env, ctl, ev, run, agent, fm, vendor, vend = world
    sent = agent.send(to=["vendor@example.test"], subject="cross", text="x")
    vendor_copy = sent["delivered_message_ids"][0]
    gid = next(p["external_id"] for p in ev.state(run)["provider_ids"]
               if p["entity_id"] == vendor_copy and p["provider"] == "gmail")  # fmt: skip
    with pytest.raises(NotFound):
        agent.resolve_external_id("gmail", "message", gid)


def test_same_folder_move_keeps_uid_and_origin(world):
    env, ctl, ev, run, agent, *_ = world
    m = agent.list_messages(folder="inbox").items[0]
    agent.move(m.id, "trash")
    uid = agent.get_message(m.id).imap_uid
    agent.trash(m.id)
    agent.move(m.id, "trash")
    assert agent.get_message(m.id).imap_uid == uid
    agent.untrash(m.id)
    assert agent.get_message(m.id).folder == "INBOX"


def test_principal_roles_are_coerced_and_unknown_roles_fail_closed(env):
    p = Principal(env.scope.tenant_id, env.scope.project_id, "agent", "run_x", "mbx_x")
    assert p.role is Role.AGENT
    assert isinstance(facade_for(env.engine, p), AgentMailbox), "string roles never fall through"
    with pytest.raises(PermissionDenied):
        Principal(env.scope.tenant_id, env.scope.project_id, "superuser")
    rogue = Principal(env.scope.tenant_id, env.scope.project_id, Role.EVALUATOR)
    object.__setattr__(rogue, "role", "nobody")
    with pytest.raises(PermissionDenied):
        facade_for(env.engine, rogue)


def test_operator_reads_validate_and_page(world):
    env, ctl, ev, run, agent, fm, *_ = world
    with pytest.raises(InvalidRequest):
        ctl.inspect_messages(run, fm, order="sideways")
    page = ev.inspect_messages(run, fm, page_size=1)
    rest = ev.inspect_messages(run, fm, cursor=page.next_cursor)
    assert len(page.items) == 1 and rest.items and page.items[0].id != rest.items[0].id
    items, nxt = ev.events_page(run, limit=2)
    assert len(items) == 2 and nxt == items[-1]["sequence"]
    assert {b["owner_id"] for b in ctl.list_mailboxes(run)} == {"finance_manager", "vendor"}
