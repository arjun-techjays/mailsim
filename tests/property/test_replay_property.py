import contextlib

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mailsim.domain.errors import ExternalDeliveryBlocked
from mailsim.engine.core import CONTROLLER

pytestmark = pytest.mark.db

OPS = st.lists(
    st.tuples(
        st.sampled_from(
            ["inject", "advance", "reply", "move", "flag", "label", "send", "delete", "reset"]
        ),
        st.integers(min_value=0, max_value=5),
    ),
    min_size=1,
    max_size=12,
)


@settings(
    max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(OPS)
def test_replay_of_any_command_sequence_reproduces_state(env, ops):
    e, sc = env.engine, env.scope
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    for op, n in ops:
        msgs = e.list_messages(sc, run, fm, actor=CONTROLLER)
        target = msgs[n % len(msgs)] if msgs else None
        if op == "inject":
            e.inject(
                sc,
                run,
                fm,
                {
                    "from": "vendor@example.test",
                    "to": ["finance@example.test"],
                    "subject": f"{'Re: ' * (n % 2)}topic {n % 3}",
                    "text": f"b{n}",
                },
            )
        elif op == "advance":
            e.advance(sc, run, n * 150)
        elif op == "reset":
            e.reset_run(sc, run)
            e.start_run(sc, run)
        elif op == "send":
            e.send(sc, run, fm, to=["vendor@example.test"], subject=f"s{n}", actor=CONTROLLER)
        elif target is not None:
            if op == "reply":
                with contextlib.suppress(ExternalDeliveryBlocked):  # non-simulated sender: blocked
                    e.reply(sc, run, fm, target.id, text="ok", actor=CONTROLLER)
            elif op == "move":
                e.move(
                    sc, run, fm, target.id, ["archive", "trash", "inbox"][n % 3], actor=CONTROLLER
                )
            elif op == "flag":
                e.set_flags(sc, run, fm, target.id, add=["FLAGGED"], actor=CONTROLLER)
            elif op == "label":
                e.modify_labels(sc, run, fm, target.id, add=[f"l{n}"], actor=CONTROLLER)
            elif op == "delete":
                e.delete(sc, run, fm, target.id, actor=CONTROLLER)
    result = e.replay_run(sc, run)
    assert result.matches
    seqs = [ev["sequence"] for ev in e.events(sc, run)]
    assert seqs == list(range(1, len(seqs) + 1))
    replayed = [ev["type"] for ev in e.events(sc, result.replay_run_id)]
    # the copy of the log, then the replay's own closing status change
    assert [ev["type"] for ev in e.events(sc, run)] + ["run.status_changed"] == replayed
