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
from tests.scenarios import write_variant

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
    # above the cap (written by the controller, which is not capped): removing is allowed,
    # adding is not
    env.engine.modify_labels(env.scope, run, fm, msg.id, add=["x", "y"], actor=CONTROLLER)
    with pytest.raises(SimulationLimitExceeded):
        env.engine.modify_labels(env.scope, run, fm, msg.id, add=["z"], remove=["x"])
    env.engine.modify_labels(env.scope, run, fm, msg.id, remove=["x"])
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
