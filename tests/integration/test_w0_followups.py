"""Phase 1 follow-ups: drift between live projection and replay, publish race, dedupe."""

import threading

import pytest
from sqlalchemy import select

from mailsim.domain.errors import NotFound
from mailsim.engine.core import CONTROLLER
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import models as m
from tests.scenarios import EXAMPLE, write_variant

pytestmark = pytest.mark.db

MAIL = {"from": "vendor@example.test", "to": ["finance@example.test"], "subject": "x", "text": "y"}


def test_replay_reproduces_engine_side_counters(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    first = env.engine.list_messages(env.scope, run, fm)[0]
    env.engine.reply(env.scope, run, fm, first.id, text="checking")
    with pytest.raises(NotFound):  # a failed agent call still consumes budget
        env.engine.get_message(env.scope, run, fm, "msg_nope")
    env.engine.inject(env.scope, run, fm, MAIL)
    env.engine.advance(env.scope, run, 700)
    live = env.engine.state(env.scope, run)
    assert live["run"]["agent_actions"] == 3 and live["run"]["id_counter"] > 0
    result = env.engine.replay_run(env.scope, run)
    assert result.matches
    replayed = env.engine.state(env.scope, result.replay_run_id)
    assert replayed == live, "live projection and replay agree, including counters"
    # a replayed run can be continued exactly like the original
    after = dict(MAIL, subject="after")
    for rid in (run, result.replay_run_id):
        env.engine.inject(env.scope, rid, fm, after)
    a = env.engine.state(env.scope, run)
    b = env.engine.state(env.scope, result.replay_run_id)
    assert a["messages"] == b["messages"] and a["run"]["id_counter"] == b["run"]["id_counter"]


def test_every_event_records_the_id_counter(env, session_factory):
    run = env.run()
    with session_factory() as s:
        counters = list(
            s.scalars(
                select(m.EventRow.id_counter_after)
                .where(m.EventRow.run_id == run)
                .order_by(m.EventRow.sequence)
            )
        )
    assert None not in counters and counters == sorted(counters)


def test_ids_drawn_after_last_event_are_recorded(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    env.engine.inject(env.scope, run, fm, MAIL)
    assert env.engine.events(env.scope, run)[-1]["type"] == "ids.reserved"
    assert env.engine.replay_run(env.scope, run).matches


def test_concurrent_first_publish_converges(env, session_factory):
    loaded = load_scenario(EXAMPLE)
    results, errors = [], []

    def worker():
        try:
            with session_factory.begin() as s:
                results.append(create_scenario_version(s, env.scope, loaded, env.blobs).id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(results)) == 1


def test_scheduled_event_duplicate_recipients_get_one_copy(env, tmp_path):
    def tweak(s):
        s["events"][0]["event"]["to"] = ["finance_manager", "finance_manager"]

    run = env.run(write_variant(tmp_path, tweak))
    env.engine.advance(env.scope, run, 600)
    fm = env.mbx(run, "finance_manager")
    msgs = [
        x
        for x in env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)
        if x.from_.email == "vendor@example.test" and x.subject.startswith("Re:")
    ]
    assert len(msgs) == 1
