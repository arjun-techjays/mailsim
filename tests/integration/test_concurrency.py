"""AC15: concurrent mailbox modifications follow the documented per-run serialisation rule."""

import threading

import pytest

from mailsim.engine.core import CONTROLLER

pytestmark = pytest.mark.db


def test_concurrent_commands_serialise_per_run_without_gaps_or_loss(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(5):
                env.engine.inject(
                    env.scope,
                    run,
                    fm,
                    {
                        "from": "vendor@example.test",
                        "to": ["finance@example.test"],
                        "subject": f"w{n}-{i}",
                        "text": "t",
                    },
                )
                env.engine.set_flags(
                    env.scope,
                    run,
                    fm,
                    env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)[0].id,
                    add=["FLAGGED"],
                    actor=CONTROLLER,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    events = env.engine.events(env.scope, run)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    subjects = {m.subject for m in env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)}
    assert {f"w{n}-{i}" for n in range(4) for i in range(5)} <= subjects
    ids = [m.id for m in env.engine.list_messages(env.scope, run, fm, actor=CONTROLLER)]
    assert len(ids) == len(set(ids)), "generated ids never collide under concurrency"
    assert env.engine.replay_run(env.scope, run).matches


def test_concurrent_agent_actions_respect_the_budget(env, tmp_path):
    from mailsim.domain.errors import SimulationLimitExceeded
    from tests.scenarios import write_variant

    run = env.run(
        write_variant(tmp_path, lambda s: s["evaluation"]["limits"].update(max_agent_actions=6))
    )
    fm = env.mbx(run, "finance_manager")
    ok, limited = [], []

    def worker() -> None:
        for _ in range(4):
            try:
                env.engine.list_folders(env.scope, run, fm)
                ok.append(1)
            except SimulationLimitExceeded:
                limited.append(1)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 6 and len(limited) == 6
    assert env.engine.run_info(env.scope, run)["agent_actions"] == 6
