"""Per-run resource caps in the engine (spec 10, issue #7): agent reads/searches, sent_copy
records, events attributed to the agent, and labels per message. Each refusal is a
SIMULATION_LIMIT_EXCEEDED naming its cap, recorded as a denied action in the run audit."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.domain.errors import NotFound, SimulationLimitExceeded
from mailsim.engine.context import use_connection_test
from mailsim.engine.core import CONTROLLER
from mailsim.store import models as m
from tests.scenarios import ROOT, write_variant

pytestmark = pytest.mark.db


def _limited(tmp_path, **limits: int) -> Any:
    def tweak(s: dict[str, Any]) -> None:
        s["agent"]["allowed_operations"] = []
        s["evaluation"]["limits"].update(limits)

    return write_variant(tmp_path, tweak)


def _counters(env, run: str) -> dict[str, int]:
    with env.session_factory() as s:
        r = s.get(m.Run, run)
        return {"actions": r.agent_actions, "reads": r.agent_reads,
                "sent_copies": r.agent_sent_copies, "events": r.agent_events}  # fmt: skip


def _agent_events(env, run: str) -> int:
    rows = env.engine.events(env.scope, run)
    epoch = max(e["epoch"] for e in rows)
    return sum(e["actor"] == "agent" and e["epoch"] == epoch for e in rows)


def _last_denied(env, run: str) -> dict[str, Any]:
    action = env.engine.actions(env.scope, run)[-1]
    assert action["actor"] == "agent" and action["outcome"] == "SIMULATION_LIMIT_EXCEEDED"
    return action


def test_read_cap_counts_guarded_reads_and_refuses_beyond(env, tmp_path):
    run = env.run(_limited(tmp_path, max_agent_reads=4))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]  # 1
    env.engine.get_message(env.scope, run, fm, msg.id)  # 2
    env.engine.search_messages(env.scope, run, fm, "invoice")  # 3: searches are reads
    with pytest.raises(NotFound):  # 4: a failed read still counts (it was attempted)
        env.engine.get_message(env.scope, run, fm, "msg_nope")
    assert _counters(env, run)["reads"] == 4
    seq_before = len(env.engine.events(env.scope, run))
    with pytest.raises(SimulationLimitExceeded, match="agent read limit of 4") as ei:
        env.engine.list_folders(env.scope, run, fm)
    assert ei.value.details == {"limit": 4, "cap": "max_agent_reads"}
    denied = _last_denied(env, run)
    assert denied["operation"] == "read"
    assert denied["result"]["details"] == {"limit": 4, "cap": "max_agent_reads"}
    assert denied["seq_end"] < denied["seq_start"]  # a refused read writes no event
    assert len(env.engine.events(env.scope, run)) == seq_before
    assert _counters(env, run)["reads"] == 4  # refused by the cap: not counted
    with pytest.raises(SimulationLimitExceeded):  # searches share the read cap
        env.engine.search_messages(env.scope, run, fm, "invoice")
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])  # writes are unaffected
    assert _counters(env, run)["actions"] == 1


def test_read_cap_ignores_operator_reads_and_connection_tests(env, tmp_path):
    run = env.run(_limited(tmp_path, max_agent_reads=1))
    fm = env.mbx(run, "finance_manager")
    env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)  # operators are not capped
    env.engine.list_folders(env.scope, run, fm)
    with pytest.raises(SimulationLimitExceeded):
        env.engine.list_folders(env.scope, run, fm)
    with use_connection_test():  # an operator's probe: never counted, so never capped
        assert env.engine.list_folders(env.scope, run, fm)
    assert _counters(env, run)["reads"] == 1
    assert env.engine.actions(env.scope, run)[-1]["actor"] == "connection_test"


def test_sent_copy_cap_stays_ungated_but_bounded(env, tmp_path):
    def tweak(s: dict[str, Any]) -> None:
        s["agent"]["allowed_operations"] = []
        s["agent"]["prohibited_operations"] = ["insert", "external_delivery"]
        s["evaluation"]["limits"].update(max_sent_copies=2, max_agent_actions=1)

    run = env.run(write_variant(tmp_path, tweak))
    fm = env.mbx(run, "finance_manager")
    for _ in range(2):  # no permission gate (insert is prohibited) and no write budget
        env.engine.sent_copy(env.scope, run, fm, "Sent", 120)
    assert _counters(env, run) == {"actions": 0, "reads": 0, "sent_copies": 2, "events": 0}
    with pytest.raises(SimulationLimitExceeded, match="sent copy limit of 2") as ei:
        env.engine.sent_copy(env.scope, run, fm, "Sent", 120)
    assert ei.value.details == {"limit": 2, "cap": "max_sent_copies"}
    assert _last_denied(env, run)["operation"] == "sent_copy"
    assert _counters(env, run)["sent_copies"] == 2


def test_event_cap_bounds_the_agents_footprint(env, tmp_path):
    run = env.run(_limited(tmp_path, max_agent_events=4))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    for flag in ("FLAGGED", "SEEN"):  # each: message.flags_changed + agent.action_counted
        env.engine.set_flags(env.scope, run, fm, msg.id, add=[flag])
    assert _counters(env, run)["events"] == 4 == _agent_events(env, run)
    with pytest.raises(SimulationLimitExceeded, match="agent event limit of 4") as ei:
        env.engine.set_flags(env.scope, run, fm, msg.id, add=["ANSWERED"])
    assert ei.value.details == {"limit": 4, "cap": "max_agent_events"}
    with pytest.raises(SimulationLimitExceeded):  # every agent operation, reads included
        env.engine.get_message(env.scope, run, fm, msg.id)
    assert _counters(env, run)["events"] == 4 == _agent_events(env, run)
    assert {a["operation"] for a in env.engine.actions(env.scope, run)
            if a["outcome"] == "SIMULATION_LIMIT_EXCEEDED"} == {"flag", "read"}  # fmt: skip
    # the simulator and the controller are not limited by the agent's cap
    env.engine.advance(env.scope, run, 600)


def test_event_cap_is_checked_before_a_command(env, tmp_path):
    """Checked before the command runs: the command that reaches the cap completes, so the
    count may exceed the cap by that one command's own events."""
    run = env.run(_limited(tmp_path, max_agent_events=3))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])  # 2 events
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["SEEN"])  # 2 more: 4 > 3
    assert _counters(env, run)["events"] == 4
    with pytest.raises(SimulationLimitExceeded):
        env.engine.set_flags(env.scope, run, fm, msg.id, add=["ANSWERED"])


def test_labels_per_message_cap(env, tmp_path):
    run = env.run(_limited(tmp_path, max_labels_per_message=2))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["a", "b"])
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["b"])  # nothing new: allowed
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["c"], remove=["a"])  # swap at cap
    actions = _counters(env, run)["actions"]
    with pytest.raises(SimulationLimitExceeded, match="labels per message limit of 2") as ei:
        env.engine.modify_labels(env.scope, run, fm, msg.id, add=["d"])
    assert ei.value.details == {"limit": 2, "cap": "max_labels_per_message"}
    denied = _last_denied(env, run)
    assert denied["operation"] == "label" and denied["input"]["add"] == ["d"]
    assert _counters(env, run)["actions"] == actions + 1  # a guarded write: it costs budget
    labels = set(env.engine.get_message(env.scope, run, fm, msg.id, actor=CONTROLLER).labels)
    assert {"b", "c"} <= labels and not labels & {"a", "d"}
    # above the cap (written by the controller, which is not capped): removing, and swapping
    # names without adding to the count (a rename, a category replacement), are allowed;
    # growing is not
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["x", "y"], actor=CONTROLLER)
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["z"], remove=["x"])  # 4 -> 4
    with pytest.raises(SimulationLimitExceeded):
        env.engine.modify_labels(env.scope, run, fm, msg.id, add=["p", "q"], remove=["z"])
    env.engine.modify_labels(env.scope, run, fm, msg.id, remove=["z"])
    other = env.engine.list_messages(env.scope, run, fm)[1]  # the cap is per message
    env.engine.modify_labels(env.scope, run, fm, other.id, add=["a", "b"])


def test_reset_clears_counters_and_replay_rebuilds_the_event_count(env, tmp_path):
    run = env.run(_limited(tmp_path, max_agent_reads=2))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])
    env.engine.sent_copy(env.scope, run, fm, "Sent", 10)
    env.engine.list_folders(env.scope, run, fm)
    with pytest.raises(SimulationLimitExceeded):
        env.engine.list_folders(env.scope, run, fm)
    assert _counters(env, run) == {"actions": 1, "reads": 2, "sent_copies": 1, "events": 2}
    replay = env.engine.replay_run(env.scope, run)
    assert replay.matches
    assert _counters(env, replay.replay_run_id)["events"] == 2  # rebuilt from the log
    env.engine.reset_run(env.scope, run)
    assert _counters(env, run) == {"actions": 0, "reads": 0, "sent_copies": 0, "events": 0}
    env.engine.start_run(env.scope, run)
    fm = env.mbx(run, "finance_manager")
    env.engine.list_folders(env.scope, run, fm)  # a new epoch: reading works again


def test_write_and_time_budgets_name_their_cap(env, tmp_path):
    run = env.run(_limited(tmp_path, max_agent_actions=1, max_virtual_duration_seconds=60))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])
    with pytest.raises(SimulationLimitExceeded) as ei:
        env.engine.set_flags(env.scope, run, fm, msg.id, add=["SEEN"])
    assert ei.value.details == {"limit": 1, "cap": "max_agent_actions"}
    with pytest.raises(SimulationLimitExceeded) as ei:
        env.engine.advance(env.scope, run, 120)
    assert ei.value.details == {"limit": 60, "cap": "max_virtual_duration_seconds"}


def test_migration_0010_backfills_agent_events_of_runs_in_flight(tmp_path):
    """Runs created before 0010 get agent_events from their log (current epoch, actor agent);
    reads and sent copies were never counted and start at 0."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from mailsim.engine.core import SimEngine
    from mailsim.scenario.loader import load_scenario
    from mailsim.scenario.service import create_scenario_version
    from mailsim.store import repos
    from mailsim.store.blob import FilesystemBlobStore
    from mailsim.store.db import run_migrations
    from tests.conftest import scratch_database

    with scratch_database("backfill") as url:
        run_migrations(url)
        db = create_engine(url)
        try:
            sf = sessionmaker(db, expire_on_commit=False)
            with sf.begin() as s:
                tenant = repos.create_tenant(s, "t")
                scope = repos.Scope(tenant.id, repos.create_project(s, tenant.id, "p").id)
            blobs = FilesystemBlobStore(tmp_path / "blobs")
            engine = SimEngine(sf, blobs)
            with sf.begin() as s:
                version = create_scenario_version(
                    s, scope, load_scenario(_limited(tmp_path, max_agent_reads=50)), blobs
                ).id
            run = engine.create_run(scope, version)
            engine.start_run(scope, run)
            fm = engine.mailbox_id_for(scope, run, "finance_manager")
            msg = engine.list_messages(scope, run, fm)[0]
            for flag in ("FLAGGED", "SEEN"):
                engine.set_flags(scope, run, fm, msg.id, add=[flag])
            with sf() as s:
                before = s.get(m.Run, run).agent_events
            assert before == 4
            db.dispose()
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.set_main_option("script_location", str(ROOT / "migrations"))
            cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
            command.downgrade(cfg, "0009")  # as if the run had been made before the caps existed
            run_migrations(url)
            with db.connect() as conn:
                row = conn.execute(
                    text("SELECT agent_events, agent_reads, agent_sent_copies FROM runs "
                         "WHERE id = :r"), {"r": run},
                ).one()  # fmt: skip
            assert tuple(row) == (before, 0, 0)
        finally:
            db.dispose()


def test_new_message_label_pre_check(env, tmp_path):
    """Gmail insert/import labelIds and IMAP APPEND keywords are checked before the message is
    stored: over the cap, a refused (budgeted) insert and nothing stored."""
    from mailsim.engine.facades import AgentMailbox

    run = env.run(_limited(tmp_path, max_labels_per_message=2))
    fm = env.mbx(run, "finance_manager")
    mb = AgentMailbox(env.engine, env.scope, run, fm)
    count = len(mb.list_messages(folder="inbox").items)
    mb.check_new_message_labels([], folder="inbox", size=10)  # nothing to check: no action
    mb.check_new_message_labels(["a", "b", "a"], folder="inbox", size=10)  # 2 distinct: fine
    assert _counters(env, run)["actions"] == 0
    with pytest.raises(SimulationLimitExceeded) as ei:
        mb.check_new_message_labels(["a", "b", "c"], folder="inbox", size=10)
    assert ei.value.details == {"limit": 2, "cap": "max_labels_per_message"}
    denied = _last_denied(env, run)
    assert denied["operation"] == "insert" and denied["input"] == {"bytes": 10, "folder": "inbox"}
    assert _counters(env, run)["actions"] == 1  # refused like the write it stands for
    assert len(mb.list_messages(folder="inbox").items) == count


def test_read_backs_of_a_committed_write_are_exempt_only_in_its_request(env, tmp_path):
    from mailsim.engine.context import is_read_back, use_agent_request

    run = env.run(_limited(tmp_path, max_agent_reads=2, max_agent_events=4))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]  # read 1
    with use_agent_request():
        env.engine.list_folders(env.scope, run, fm)  # read 2: before any write, it counts
        with pytest.raises(SimulationLimitExceeded):  # and the cap applies
            env.engine.list_folders(env.scope, run, fm)
        assert not is_read_back()
        env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])  # events: 2
        env.engine.set_flags(env.scope, run, fm, msg.id, add=["SEEN"])  # events: 4 (= cap)
        assert is_read_back()
        for _ in range(3):  # the read-back: recorded, not counted, not refused by either cap
            env.engine.get_message(env.scope, run, fm, msg.id)
        with pytest.raises(SimulationLimitExceeded):  # writes stay capped
            env.engine.set_flags(env.scope, run, fm, msg.id, add=["ANSWERED"])
        with use_agent_request():  # a new request starts without the exemption
            assert not is_read_back()
            with pytest.raises(SimulationLimitExceeded):
                env.engine.get_message(env.scope, run, fm, msg.id)
    assert not is_read_back()
    with pytest.raises(SimulationLimitExceeded):  # outside any request: no exemption either
        env.engine.get_message(env.scope, run, fm, msg.id)
    assert _counters(env, run)["reads"] == 2
    reads = [a for a in env.engine.actions(env.scope, run) if a["operation"] == "read"]
    assert sum(a["outcome"] == "ok" for a in reads) == 5  # the read-backs are in the audit


def test_a_refused_write_does_not_open_the_exemption(env, tmp_path):
    from mailsim.engine.context import is_read_back, use_agent_request

    run = env.run(_limited(tmp_path, max_agent_reads=1, max_agent_actions=2))
    fm = env.mbx(run, "finance_manager")
    msg = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.set_flags(env.scope, run, fm, msg.id, add=["FLAGGED"])
    with use_agent_request():
        with pytest.raises(NotFound):  # a write that fails inside does not open it
            env.engine.move(env.scope, run, fm, "msg_nope", "archive")  # budget now spent
        with pytest.raises(SimulationLimitExceeded):  # nor does one the guard refuses
            env.engine.set_flags(env.scope, run, fm, msg.id, add=["SEEN"])
        assert not is_read_back()
        with pytest.raises(SimulationLimitExceeded):
            env.engine.list_folders(env.scope, run, fm)
    with use_agent_request(), use_connection_test():
        env.engine.list_folders(env.scope, run, fm)  # a probe's read is not a write either
        assert not is_read_back()


def test_insert_applies_flags_and_labels_in_one_write_or_none(env, tmp_path):
    from mailsim.domain.errors import InvalidRequest, PermissionDenied

    run = env.run(_limited(tmp_path, max_labels_per_message=2))
    fm = env.mbx(run, "finance_manager")
    raw = (b"From: vendor@example.test\r\nTo: finance@example.test\r\nSubject: in\r\n"
           b"Message-ID: <ins@example.test>\r\n\r\nbody\r\n")  # fmt: skip
    count = len(env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER))
    for kw, exc in ((dict(flags=["BOGUS"]), InvalidRequest), (dict(labels=[" "]), InvalidRequest),
                    (dict(flags=["DRAFT"]), PermissionDenied),
                    (dict(labels=["a", "b", "c"]), SimulationLimitExceeded)):  # fmt: skip
        with pytest.raises(exc):
            env.engine.insert_message(env.scope, run, fm, raw, **kw)
    assert len(env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)) == count
    mid = env.engine.insert_message(env.scope, run, fm, raw, flags=["SEEN", "FLAGGED"],
                                    labels=["a", "b", "a"])["message_id"]  # fmt: skip
    msg = env.engine.get_message(env.scope, run, fm, mid, actor=CONTROLLER)
    assert {"SEEN", "FLAGGED"} <= set(msg.flags) and {"a", "b"} <= set(msg.labels)
    inserts = [a for a in env.engine.actions(env.scope, run) if a["operation"] == "insert"]
    assert [a["outcome"] for a in inserts] == ["INVALID_REQUEST", "INVALID_REQUEST",
                                               "PERMISSION_DENIED", "SIMULATION_LIMIT_EXCEEDED",
                                               "ok"]  # fmt: skip
    assert inserts[-1]["input"] == {"bytes": len(raw), "folder": "inbox",
                                    "flags": ["FLAGGED", "SEEN"], "labels": ["a", "b"]}  # fmt: skip
    assert _counters(env, run)["actions"] == 5  # one budget unit per insert, refusals included
    env.engine.insert_message(env.scope, run, fm, raw, labels=["x", "y", "z"], actor=CONTROLLER)
