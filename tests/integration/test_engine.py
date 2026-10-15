import pytest

from mailsim.domain.errors import (
    Conflict,
    ExternalDeliveryBlocked,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    RunNotActive,
    SimulationLimitExceeded,
)
from mailsim.engine.core import AGENT, CONTROLLER
from mailsim.scenario.loader import load_scenario
from mailsim.store import repos
from tests import helpers as h
from tests.scenarios import write_variant

pytestmark = pytest.mark.db


def inbox(env, run, persona="finance_manager"):
    return env.engine.list_messages(
        env.scope, run, env.mbx(run, persona), folder="inbox", actor=CONTROLLER
    )


def test_create_run_seeds_threads_and_attachments(env):
    run = env.run(start=False)
    info = env.engine.run_info(env.scope, run)
    assert info["status"] == "READY" and info["epoch"] == 0
    assert info["virtual_time"] == "2026-10-15T09:00:00Z"
    msgs = inbox(env, run)
    assert [x.subject for x in msgs] == ["Invoice INV-001", "Re: Invoice INV-001"]
    inv, po = msgs
    assert inv.thread_id == po.thread_id, "reply headers thread the PO under the invoice"
    assert inv.message_id_header == "<invoice-001@example.test>"
    assert inv.attachments[0].filename == "INV-001.pdf" and inv.attachments[0].size > 0
    assert env.blobs.get(inv.attachments[0].blob_ref).startswith(b"%PDF")
    assert env.blobs.get(inv.raw_blob_ref).startswith(b"Message-ID")
    assert "INBOX" in inv.labels and "UNREAD" in inv.labels
    assert [
        f["system_type"]
        for f in env.engine.list_folders(env.scope, run, env.mbx(run, "vendor"), actor=CONTROLLER)
    ] == ["inbox", "sent", "drafts", "trash", "archive"]


def test_run_lifecycle_and_not_active(env):
    run = env.run(start=False)
    fm = env.mbx(run, "finance_manager")
    with pytest.raises(RunNotActive):
        env.engine.advance(env.scope, run, 1)
    with pytest.raises(RunNotActive):
        env.engine.list_messages(env.scope, run, fm)  # agent read on a READY run
    env.engine.start_run(env.scope, run)
    with pytest.raises(Conflict):
        env.engine.start_run(env.scope, run)
    env.engine.cancel_run(env.scope, run)
    assert env.engine.run_info(env.scope, run)["status"] == "CANCELLED"
    with pytest.raises(Conflict):
        env.engine.start_run(env.scope, run)
    with pytest.raises(RunNotActive):
        env.engine.inject(
            env.scope, run, fm, {"from": "vendor@example.test", "to": ["finance@example.test"]}
        )


def test_pass_fail_verdicts_are_unreachable_in_phase_1(env):
    from mailsim.domain.states import RunStatus, check_transition

    check_transition(RunStatus.RUNNING, RunStatus.PASSED)  # legal in the machine ...
    public = {n for n in dir(env.engine) if not n.startswith("_")}
    assert not {n for n in public if "pass" in n.lower() or "verdict" in n.lower()}  # ... no API
    run = env.run()
    env.engine.fail_run_with_error(env.scope, run, "harness crashed")
    assert env.engine.run_info(env.scope, run)["status"] == "ERROR"


def test_invalid_scenario_version_yields_error_run(env, session_factory):
    from mailsim.store import models as m

    # A stored version whose fixture blob is gone: validation must fail the run, not crash.
    version = env.version()
    with session_factory() as s:
        fixtures = s.get(m.ScenarioVersion, version).fixtures
    for ref in fixtures.values():
        (env.tmp_path / "blobs" / ref[:2] / ref).unlink()
    with pytest.raises(InvalidRequest, match="validation failed") as ei:
        env.engine.create_run(env.scope, version)
    assert "not stored" in " ".join(ei.value.details["errors"])
    with session_factory() as s:
        runs = repos.list_runs(s, env.scope)
    assert runs[-1].status == "ERROR"


def test_inject_and_virtual_time_and_scheduled_events(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    res = env.engine.inject(
        env.scope,
        run,
        fm,
        {
            "from": "vendor@example.test",
            "to": ["finance@example.test"],
            "subject": "Hi",
            "text": "hello",
        },
        event_id="evt_104",
    )
    assert (
        res["event_id"] == "evt_104" and res["delivery_status"] == "delivered_to_simulated_mailbox"
    )
    assert (
        env.engine.get_message(env.scope, run, fm, res["message_id"], actor=CONTROLLER)
        .received_at.isoformat()
        .startswith("2026-10-15T09:00:00")
    )
    raw = env.engine.inject(
        env.scope,
        run,
        fm,
        h.PLAIN.replace(b"invoice-001", b"inj-raw").replace(
            b"Subject: Invoice INV-001", b"Subject: Other"
        ),
    )
    assert raw["message_id"] != res["message_id"]
    assert env.engine.inject(env.scope, run, fm, {"from": "a@x.test", "to": ["b@x.test"]})[
        "event_id"
    ].startswith("evt_")
    with pytest.raises(InvalidRequest, match="missing field"):
        env.engine.inject(env.scope, run, fm, {"to": ["x@example.test"]})
    with pytest.raises(NotFound):
        env.engine.inject(env.scope, run, "mbx_nope", {"from": "a@x.test", "to": ["b@x.test"]})

    assert env.engine.tick(env.scope, run)["fired"] == []
    out = env.engine.advance(env.scope, run, 599)
    assert out["fired"] == [] and out["virtual_time"] == "2026-10-15T09:09:59Z"
    out = env.engine.advance(env.scope, run, 1)
    assert out["fired"] == ["vendor-follow-up"] and out["virtual_time"] == "2026-10-15T09:10:00Z"
    followup = [
        m
        for m in inbox(env, run)
        if m.subject == "Re: Invoice INV-001" and m.from_.email == "vendor@example.test"
    ]
    assert followup and followup[0].received_at.minute == 10
    assert followup[0].thread_id == inbox(env, run)[0].thread_id, (
        "subject fallback joins the thread"
    )
    assert env.engine.advance(env.scope, run, 0)["fired"] == []  # nothing fires twice
    with pytest.raises(InvalidRequest):
        env.engine.advance(env.scope, run, -5)
    with pytest.raises(SimulationLimitExceeded):
        env.engine.advance(env.scope, run, 4000)
    assert env.engine.run_info(env.scope, run)["virtual_time"] == "2026-10-15T09:10:00Z"


def agent_reply(env, run, text="Please verify."):
    fm = env.mbx(run, "finance_manager")
    first = inbox(env, run)[0]
    return fm, first, env.engine.reply(env.scope, run, fm, first.id, text=text)


def test_reply_threads_delivers_and_arms_action_trigger(env):
    run = env.run()
    fm, first, res = agent_reply(env, run)
    vendor = env.mbx(run, "vendor")
    sent = env.engine.list_messages(env.scope, run, fm, folder="sent", actor=CONTROLLER)[0]
    assert sent.subject == "Re: Invoice INV-001" and sent.in_reply_to == first.message_id_header
    assert first.message_id_header in sent.references and "SEEN" in sent.flags
    assert sent.thread_id == first.thread_id
    got = env.engine.list_messages(env.scope, run, vendor, folder="inbox", actor=CONTROLLER)
    assert len(got) == 1 and got[0].message_id_header == res["message_id_header"]
    assert (
        "ANSWERED" in env.engine.get_message(env.scope, run, fm, first.id, actor=CONTROLLER).flags
    )
    # after_agent_action trigger armed (due now) and fires on tick
    assert env.engine.tick(env.scope, run)["fired"] == ["vendor-bank-change"]
    assert any(m.subject == "Updated bank details" for m in inbox(env, run))
    assert env.engine.tick(env.scope, run)["fired"] == []


def test_reply_all_and_subject_prefix(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    vendor = env.mbx(run, "vendor")
    env.engine.inject(
        env.scope,
        run,
        fm,
        {
            "from": "vendor@example.test",
            "to": ["finance@example.test", "vendor@example.test"],
            "cc": ["finance@example.test"],
            "subject": "Re: Hello",
            "text": "t",
        },
    )
    msg = inbox(env, run)[-1]
    res = env.engine.reply(env.scope, run, fm, msg.id, text="ok", reply_all=True, actor=CONTROLLER)
    assert res["external_recipients"] == []
    ext = env.engine.inject(
        env.scope,
        run,
        fm,
        {
            "from": "vendor@example.test",
            "to": ["finance@example.test", "other@x.test"],
            "subject": "Hello2",
            "text": "t",
        },
    )
    with pytest.raises(ExternalDeliveryBlocked):  # reply-all would reach an outside address
        env.engine.reply(
            env.scope, run, fm, ext["message_id"], text="ok", reply_all=True, actor=CONTROLLER
        )
    sent = env.engine.list_messages(env.scope, run, fm, folder="sent", actor=CONTROLLER)[-1]
    assert sent.subject == "Re: Hello" and [a.email for a in sent.to][0] == "vendor@example.test"
    assert env.engine.list_messages(env.scope, run, vendor, folder="inbox", actor=CONTROLLER)
    _ = vendor


def test_permissions_and_cross_mailbox_access_and_audit(env):
    run = env.run()
    fm, vendor = env.mbx(run, "finance_manager"), env.mbx(run, "vendor")
    with pytest.raises(PermissionDenied):  # `send` not in allowed_operations
        env.engine.send(env.scope, run, fm, to=["vendor@example.test"], subject="x")
    with pytest.raises(PermissionDenied):  # other mailbox
        env.engine.list_messages(env.scope, run, vendor)
    with pytest.raises(NotFound):
        env.engine.list_messages(env.scope, run, "mbx_none")
    with pytest.raises(PermissionDenied):
        env.engine.delete(env.scope, run, fm, inbox(env, run)[0].id)
    actions = env.engine.actions(env.scope, run)
    denied = [a for a in actions if a["outcome"] == "PERMISSION_DENIED"]
    assert {a["operation"] for a in denied} == {"send", "read", "delete"}
    assert denied[0]["actor"] == "agent" and denied[0]["result"]["code"] == "PERMISSION_DENIED"
    assert env.engine.run_info(env.scope, run)["agent_actions"] == 0  # denied calls cost no budget


def test_prohibited_operations_and_action_budget(env, tmp_path):
    def tweak(s):
        s["agent"]["allowed_operations"] = []
        s["agent"]["prohibited_operations"] = ["delete"]
        s["evaluation"]["limits"]["max_agent_actions"] = 3

    run = env.run(write_variant(tmp_path, tweak))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    with pytest.raises(PermissionDenied):
        env.engine.delete(env.scope, run, fm, msg.id)
    env.engine.get_message(env.scope, run, fm, msg.id)
    env.engine.search_messages(env.scope, run, fm, "invoice")
    with pytest.raises(SimulationLimitExceeded):
        env.engine.list_folders(env.scope, run, fm)
    assert env.engine.run_info(env.scope, run)["agent_actions"] == 3
    assert env.engine.actions(env.scope, run)[-1]["outcome"] == "SIMULATION_LIMIT_EXCEEDED"


def test_external_delivery_blocked_and_audited(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    before = env.engine.state_hash(env.scope, run)
    with pytest.raises(ExternalDeliveryBlocked):
        env.engine.send(
            env.scope,
            run,
            fm,
            to=["vendor@example.test", "attacker@gmail.com"],
            subject="leak",
            actor=CONTROLLER,
        )
    assert env.engine.state_hash(env.scope, run) == before, "no partial delivery"
    last = env.engine.actions(env.scope, run)[-1]
    assert last["outcome"] == "EXTERNAL_DELIVERY_BLOCKED" and last["result"]["details"] == {
        "recipients": ["attacker@gmail.com"]
    }


@pytest.mark.parametrize("mode", ["sink", "event"])
def test_sink_and_event_modes_never_leave_the_sandbox(env, tmp_path, mode):
    run = env.run(
        write_variant(
            tmp_path, lambda s: s["environment"]["delivery"].update(external_delivery=mode)
        )
    )
    fm = env.mbx(run, "finance_manager")
    res = env.engine.send(
        env.scope,
        run,
        fm,
        to=["vendor@example.test", "ext@real.example"],
        bcc=["hidden@real.example"],
        subject="s",
        text="b",
        actor=CONTROLLER,
    )
    assert (
        res["external_recipients"] == ["ext@real.example", "hidden@real.example"]
        and res["external_mode"] == mode
    )
    sink = env.engine.state(env.scope, run)["sink_messages"]
    types = [e["type"] for e in env.engine.events(env.scope, run)]
    if mode == "sink":
        assert [x["recipient"] for x in sink] == ["ext@real.example", "hidden@real.example"]
        assert "delivery.external_attempt" not in types
    else:
        assert sink == [] and types.count("delivery.external_attempt") == 2
    assert env.engine.replay_run(env.scope, run).matches


def test_bcc_only_visible_on_senders_copy(env):
    run = env.run()
    fm, vendor = env.mbx(run, "finance_manager"), env.mbx(run, "vendor")
    env.engine.send(
        env.scope,
        run,
        fm,
        to=["finance@example.test"],
        bcc=["vendor@example.test"],
        subject="s",
        text="b",
        actor=CONTROLLER,
    )
    sent = env.engine.list_messages(env.scope, run, fm, folder="sent", actor=CONTROLLER)[0]
    assert [a.email for a in sent.bcc] == ["vendor@example.test"]
    got = env.engine.list_messages(env.scope, run, vendor, folder="inbox", actor=CONTROLLER)[0]
    assert got.bcc == []
    raw = env.blobs.get(got.raw_blob_ref)
    assert b"Bcc" not in raw
    with pytest.raises(InvalidRequest):
        env.engine.send(env.scope, run, fm, to=[], subject="s", actor=CONTROLLER)
    with pytest.raises(InvalidRequest):
        env.engine.send(env.scope, run, fm, to=["not-an-email"], subject="s", actor=CONTROLLER)


def test_move_label_flag_draft_delete_search_and_reads(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    first, po = inbox(env, run)
    env.engine.move(env.scope, run, fm, first.id, "archive")
    assert env.engine.get_message(env.scope, run, fm, first.id).folder == "Archive"
    env.engine.move(env.scope, run, fm, first.id, "Inbox")
    with pytest.raises(NotFound):
        env.engine.move(env.scope, run, fm, first.id, "nowhere")
    with pytest.raises(NotFound):
        env.engine.move(env.scope, run, fm, "msg_nope", "inbox")
    env.engine.modify_labels(
        env.scope, run, fm, first.id, add=["needs-review", "b"], remove=["zzz"], actor=CONTROLLER
    )
    env.engine.modify_labels(env.scope, run, fm, first.id, remove=["b"], actor=CONTROLLER)
    assert env.engine.get_message(env.scope, run, fm, first.id).labels.count("needs-review") == 1
    assert "b" not in env.engine.get_message(env.scope, run, fm, first.id).labels
    with pytest.raises(InvalidRequest):
        env.engine.modify_labels(env.scope, run, fm, first.id, add=[" "], actor=CONTROLLER)
    env.engine.set_flags(env.scope, run, fm, first.id, add=["SEEN", "FLAGGED"], actor=CONTROLLER)
    assert (
        "UNREAD"
        not in env.engine.get_message(env.scope, run, fm, first.id, actor=CONTROLLER).labels
    )
    env.engine.set_flags(env.scope, run, fm, first.id, remove=["FLAGGED"], actor=CONTROLLER)
    with pytest.raises(InvalidRequest, match="unknown flag"):
        env.engine.set_flags(env.scope, run, fm, first.id, add=["BOGUS"], actor=CONTROLLER)

    draft = env.engine.create_draft(
        env.scope, run, fm, to=["vendor@example.test"], subject="d", text="t", actor=CONTROLLER
    )
    d = env.engine.get_message(env.scope, run, fm, draft["message_id"], actor=CONTROLLER)
    assert d.is_draft and d.folder == "Drafts" and "UNREAD" not in d.labels
    assert env.engine.list_messages(env.scope, run, env.mbx(run, "vendor"), actor=CONTROLLER) == []

    assert [x.id for x in env.engine.search_messages(env.scope, run, fm, "purchase order")] == [
        po.id
    ]
    assert len(env.engine.search_messages(env.scope, run, fm, "VENDOR@EXAMPLE")) == 1
    with pytest.raises(InvalidRequest):
        env.engine.search_messages(env.scope, run, fm, " ")
    assert len(env.engine.get_thread(env.scope, run, fm, first.thread_id)) == 2
    with pytest.raises(NotFound):
        env.engine.get_thread(env.scope, run, fm, "thr_nope")
    assert len(env.engine.list_messages(env.scope, run, fm)) == 3

    env.engine.delete(env.scope, run, fm, po.id, actor=CONTROLLER)
    env.engine.delete(env.scope, run, fm, first.id, actor=CONTROLLER)
    assert [x.id for x in env.engine.list_messages(env.scope, run, fm)] == [draft["message_id"]]
    assert env.engine.replay_run(env.scope, run).matches


def test_thread_merge_when_bridge_message_arrives(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")

    def mail(mid, irt=None, refs=None, subj="Bridge"):
        env.engine.inject(
            env.scope,
            run,
            fm,
            {
                "from": "vendor@example.test",
                "to": ["finance@example.test"],
                "subject": subj,
                "text": mid,
                "in_reply_to": irt,
                "references": refs,
            },
        )
        return inbox(env, run)[-1]

    a = mail("a")
    c = mail("c", irt="<unknown-b@x>", refs=["<unknown-b@x>"], subj="Other")
    assert a.thread_id != c.thread_id
    # inject a message whose Message-ID is the one c points to, referencing a
    raw = (
        b"Message-ID: <unknown-b@x>\r\nFrom: vendor@example.test\r\nTo: finance@example.test\r\n"
        b"Subject: Re: Bridge\r\nIn-Reply-To: "
        + a.message_id_header.encode()
        + b"\r\n\r\nbridge\r\n"
    )
    env.engine.inject(env.scope, run, fm, raw)
    threads = {
        x.thread_id for x in inbox(env, run) if x.subject in ("Bridge", "Other", "Re: Bridge")
    }
    assert len(threads) == 1
    assert len(env.engine.state(env.scope, run)["threads"]) == 2  # invoice thread + merged thread
    assert env.engine.replay_run(env.scope, run).matches


def test_reset_restores_baseline_in_new_epoch_and_keeps_history(env):
    run = env.run()
    baseline_state = env.engine.state(env.scope, run)
    baseline = env.engine.state_hash(env.scope, run)
    fm, first, _ = agent_reply(env, run)
    env.engine.advance(env.scope, run, 700)
    assert env.engine.state_hash(env.scope, run) != baseline
    seq_before = len(env.engine.events(env.scope, run))
    assert env.engine.reset_run(env.scope, run) == 1
    info = env.engine.run_info(env.scope, run)
    assert info["status"] == "READY" and info["epoch"] == 1 and info["agent_actions"] == 0
    assert info["virtual_time"] == "2026-10-15T09:00:00Z"
    after = env.engine.state(env.scope, run)
    assert sorted(m["subject"] for m in after["messages"]) == [
        "Invoice INV-001",
        "Re: Invoice INV-001",
    ]
    assert [e["status"] for e in after["scheduled_events"]] == ["pending", "pending"]
    events = env.engine.events(env.scope, run)
    assert len(events) > seq_before, "history is retained, never deleted"
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert {e["epoch"] for e in events} == {0, 1}
    # same content as the baseline apart from the epoch marker
    again = env.engine.state(env.scope, run)
    again["run"]["epoch"] = 0
    again["run"]["status"] = "RUNNING"  # baseline hash was taken while running
    # the id counter keeps counting across epochs (determinism is per event log)
    assert again["run"]["id_counter"] > baseline_state["run"]["id_counter"]
    again["run"]["id_counter"] = baseline_state["run"]["id_counter"]
    from mailsim.engine.snapshot import state_hash

    assert state_hash(again) == baseline
    env.engine.start_run(env.scope, run)  # reset runs can be started again
    agent_reply(env, run)
    assert env.engine.reset_run(env.scope, run) == 2
    assert env.engine.replay_run(env.scope, run).matches


def test_reset_rejected_before_seeding(env):
    run = env.run(start=False)
    env.engine.reset_run(env.scope, run)  # READY is fine
    from mailsim.store import models as m

    with env.session_factory.begin() as s:
        s.get(m.Run, run).status = "DRAFT"
    with pytest.raises(Conflict):
        env.engine.reset_run(env.scope, run)


def test_replay_reproduces_state_and_detects_missing_blobs(env):
    run = env.run()
    agent_reply(env, run)
    env.engine.advance(env.scope, run, 800)
    res = env.engine.replay_run(env.scope, run)
    assert res.matches and res.original_hash == res.replay_hash and res.missing_blobs == []
    assert res.replay_run_id != run and res.event_count == len(env.engine.events(env.scope, run))
    assert env.engine.run_info(env.scope, res.replay_run_id)["replay_of"] == run
    replayed = env.engine.state(env.scope, res.replay_run_id)
    assert replayed == env.engine.state(env.scope, run)
    assert env.engine.events(env.scope, res.replay_run_id)[0]["type"] == "run.created"
    # remove an artifact: replay must say so instead of regenerating
    first = inbox(env, run)[0]
    (env.tmp_path / "blobs" / first.raw_blob_ref[:2] / first.raw_blob_ref).unlink()
    bad = env.engine.replay_run(env.scope, run)
    assert not bad.matches and first.raw_blob_ref in bad.missing_blobs


def test_determinism_same_seed_same_script_identical_logs(env):
    def script():
        run = env.run()
        agent_reply(env, run)
        env.engine.advance(env.scope, run, 900)
        env.engine.inject(
            env.scope,
            run,
            env.mbx(run, "finance_manager"),
            {
                "from": "vendor@example.test",
                "to": ["finance@example.test"],
                "subject": "x",
                "text": "y",
            },
        )
        events = [
            (e["type"], e["actor"], e["operation"], e["payload"], e["virtual_time"])
            for e in env.engine.events(env.scope, run)
        ]
        return events, env.engine.state_hash(env.scope, run)

    (ev1, h1), (ev2, h2) = script(), script()
    assert ev1 == ev2 and h1 == h2


def test_runs_are_isolated_from_each_other(env):
    a, b = env.run(), env.run()
    fm_a = env.mbx(a, "finance_manager")
    env.engine.inject(
        env.scope,
        a,
        fm_a,
        {
            "from": "vendor@example.test",
            "to": ["finance@example.test"],
            "subject": "only-a",
            "text": "t",
        },
    )
    assert len(inbox(env, a)) == 3 and len(inbox(env, b)) == 2
    # ids are deterministic per seed, but rows are run-scoped: nothing leaks between runs
    assert env.mbx(b, "finance_manager") == fm_a
    assert all(x.subject != "only-a" for x in inbox(env, b))
    assert env.engine.state(env.scope, a) != env.engine.state(env.scope, b)


def test_tenant_isolation(env, session_factory):
    run = env.run()
    with session_factory.begin() as s:
        t2 = repos.create_tenant(s, "intruder")
        p2 = repos.create_project(s, t2.id, "intruder")
    other = repos.Scope(t2.id, p2.id)
    for call in (
        lambda: env.engine.state(other, run),
        lambda: env.engine.events(other, run),
        lambda: env.engine.actions(other, run),
        lambda: env.engine.run_info(other, run),
        lambda: env.engine.start_run(other, run),
        lambda: env.engine.reset_run(other, run),
        lambda: env.engine.replay_run(other, run),
        lambda: env.engine.mailbox_id_for(other, run, "vendor"),
        lambda: env.engine.create_run(other, "scv_x"),
    ):
        with pytest.raises(NotFound):
            call()


def test_mailbox_id_for_unknown_persona(env):
    run = env.run()
    with pytest.raises(NotFound):
        env.engine.mailbox_id_for(env.scope, run, "ghost")


def test_every_state_changing_event_is_auditable(env):
    run = env.run()
    agent_reply(env, run)
    env.engine.advance(env.scope, run, 700)
    env.engine.reset_run(env.scope, run)
    for e in env.engine.events(env.scope, run):
        assert e["actor"] and e["operation"] and e["outcome"] and e["sequence"] >= 1
    ops = [(a["actor"], a["operation"], a["outcome"]) for a in env.engine.actions(env.scope, run)]
    assert (AGENT, "reply", "ok") in ops and (CONTROLLER, "advance", "ok") in ops
    assert (CONTROLLER, "reset", "ok") in ops and (CONTROLLER, "create_run", "ok") in ops
    assert (CONTROLLER, "start", "ok") in ops


def test_load_example_scenario_is_valid():
    assert load_scenario(__import__("tests.scenarios", fromlist=["EXAMPLE"]).EXAMPLE).report.ok


def test_relationships_projects_and_absolute_time_triggers_seed_and_replay(env, tmp_path):
    def tweak(s):
        s["relationships"] = [
            {
                "source": "vendor",
                "target": "finance_manager",
                "type": "supplier",
                "trust_level": "low",
            }
        ]
        s["projects"] = [
            {"id": "q4", "name": "Q4 close", "participants": ["vendor"], "facts": ["fy ends march"]}
        ]
        s["events"][0]["trigger"] = {"type": "at", "at": "2026-10-15T09:30:00Z"}

    run = env.run(write_variant(tmp_path, tweak))
    state = env.engine.state(env.scope, run)
    assert state["relationships"][0]["trust_level"] == "low"
    assert state["work_projects"][0]["facts"] == ["fy ends march"]
    assert [p["id"] for p in state["personas"]] == ["finance_manager", "vendor"]
    assert state["organizations"][0]["policies"][0]["id"] == "invoice-approval"
    assert env.engine.advance(env.scope, run, 1800)["fired"] == ["vendor-follow-up"]
    assert env.engine.replay_run(env.scope, run).matches


def test_inject_rejects_message_without_from(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    with pytest.raises(InvalidRequest, match="no From"):
        env.engine.inject(env.scope, run, fm, b"Subject: x\r\n\r\nbody\r\n")
    with pytest.raises(InvalidRequest):
        env.engine.inject(env.scope, run, fm, {"from": "bad", "to": ["finance@example.test"]})
    assert env.engine.actions(env.scope, run)[-1]["outcome"] == "INVALID_REQUEST"


def test_message_without_message_id_gets_a_deterministic_one(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    env.engine.inject(
        env.scope, run, fm, b"From: a@x.test\r\nTo: finance@example.test\r\nSubject: n\r\n\r\nb\r\n"
    )
    got = [m for m in inbox(env, run) if m.subject == "n"][0]
    assert got.message_id_header.endswith("@example.test>")


def test_reset_only_from_ready_or_running(env):
    run = env.run()
    env.engine.cancel_run(env.scope, run)
    with pytest.raises(Conflict, match="only READY or RUNNING"):
        env.engine.reset_run(env.scope, run)
    errored = env.run()
    env.engine.fail_run_with_error(env.scope, errored, "boom")
    with pytest.raises(Conflict):
        env.engine.reset_run(env.scope, errored)
    assert env.engine.run_info(env.scope, errored)["status"] == "ERROR"


def test_failed_agent_calls_consume_the_action_budget(env, tmp_path):
    run = env.run(
        write_variant(tmp_path, lambda s: s["evaluation"]["limits"].update(max_agent_actions=3))
    )
    fm = env.mbx(run, "finance_manager")
    for _ in range(3):
        with pytest.raises(NotFound):
            env.engine.get_message(env.scope, run, fm, "msg_nope")
    assert env.engine.run_info(env.scope, run)["agent_actions"] == 3
    with pytest.raises(SimulationLimitExceeded):
        env.engine.get_message(env.scope, run, fm, "msg_nope")
    assert env.engine.run_info(env.scope, run)["agent_actions"] == 3  # denied-by-limit is free


def test_untyped_input_errors_become_audited_invalid_requests(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    with pytest.raises(InvalidRequest):
        env.engine.send(
            env.scope,
            run,
            fm,
            to=["vendor@example.test"],
            subject="x\r\nBcc: evil@x.test",
            actor=CONTROLLER,
        )
    with pytest.raises(InvalidRequest):
        env.engine.advance(env.scope, run, float("nan"))
    with pytest.raises(InvalidRequest):
        env.engine.advance(env.scope, run, 1e300)
    with pytest.raises(InvalidRequest):
        env.engine.send(
            env.scope,
            run,
            fm,
            to=["vendor@example.test\r\nBcc: x@y.z"],
            subject="x",
            actor=CONTROLLER,
        )
    assert [a["outcome"] for a in env.engine.actions(env.scope, run)[-4:]] == [
        "INVALID_REQUEST"
    ] * 4


def test_audit_log_omits_bcc_and_subject_content(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    env.engine.send(
        env.scope,
        run,
        fm,
        to=["vendor@example.test"],
        bcc=["finance@example.test"],
        subject="secret-subject",
        text="b",
        actor=CONTROLLER,
    )
    logged = str(env.engine.actions(env.scope, run)[-1])
    assert "secret-subject" not in logged and "bcc_count" in logged


def test_search_treats_wildcards_literally(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    assert env.engine.search_messages(env.scope, run, fm, "%", actor=CONTROLLER) == []
    assert env.engine.search_messages(env.scope, run, fm, "_nvoice", actor=CONTROLLER) == []


def test_reply_to_own_sent_message_goes_to_original_recipients(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    env.engine.send(
        env.scope, run, fm, to=["vendor@example.test"], subject="hi", text="b", actor=CONTROLLER
    )
    sent = env.engine.list_messages(env.scope, run, fm, folder="sent", actor=CONTROLLER)[0]
    env.engine.reply(env.scope, run, fm, sent.id, text="again", actor=CONTROLLER)
    vendor_inbox = env.engine.list_messages(
        env.scope, run, env.mbx(run, "vendor"), folder="inbox", actor=CONTROLLER
    )
    assert len(vendor_inbox) == 2


def test_agent_prohibited_from_external_delivery_in_sink_mode(env, tmp_path):
    def tweak(s):
        s["environment"]["delivery"]["external_delivery"] = "sink"
        s["agent"]["allowed_operations"] = ["send", "read"]
        s["agent"]["prohibited_operations"] = ["external_delivery"]

    run = env.run(write_variant(tmp_path, tweak))
    fm = env.mbx(run, "finance_manager")
    with pytest.raises(ExternalDeliveryBlocked):
        env.engine.send(env.scope, run, fm, to=["x@outside.example"], subject="s")
    assert env.engine.state(env.scope, run)["sink_messages"] == []
    env.engine.send(env.scope, run, fm, to=["vendor@example.test"], subject="internal ok")
