import threading
from datetime import UTC

import pytest
from sqlalchemy import func, select

from mailsim.engine import log
from mailsim.engine.log import Event
from mailsim.store import models as m
from mailsim.store import repos

pytestmark = pytest.mark.db


@pytest.fixture()
def scoped_run(session_factory):
    with session_factory.begin() as s:
        t = repos.create_tenant(s, "t")
        p = repos.create_project(s, t.id, "p")
        run = m.Run(id=f"run_{t.id[-8:]}", tenant_id=t.id, project_id=p.id, status="DRAFT", seed=5)
        s.add(run)
    return repos.Scope(t.id, p.id), run.id


def test_append_assigns_gapless_sequences_and_projects(session_factory, scoped_run):
    scope, run_id = scoped_run
    with session_factory.begin() as s:
        run = log.lock_run(s, scope, run_id)
        a = log.append(s, run, Event("clock.set", {"at": "2026-10-15T09:00:00Z", "initial": True}))
        b = log.append(s, run, Event("clock.advanced", {"to": "2026-10-15T09:01:00Z"}))
        c = log.append(
            s, run, Event("run.status_changed", {"to": "READY"}, actor="sim", operation="x")
        )
        d = log.append(s, run, Event("unknown.type", {}))
        log.append(s, run, Event("run.seeded", {}))
        assert (a, b, c, d) == (1, 2, 3, 4)
    with session_factory() as s:
        run = repos.get_run(s, scope, run_id)
        # timestamptz is read back in the session's TimeZone (issue #6): compare in UTC
        assert run.status == "READY" and run.virtual_time.astimezone(UTC).minute == 1
        assert run.start_time.astimezone(UTC).minute == 0 and run.next_seq == 6
        ev = (
            s.execute(
                select(m.EventRow).where(m.EventRow.run_id == run_id).order_by(m.EventRow.sequence)
            )
            .scalars()
            .all()
        )
        assert [e.sequence for e in ev] == [1, 2, 3, 4, 5]
        assert ev[2].actor == "sim" and ev[0].operation == "clock.set"


def test_rollback_leaves_no_event_or_projection(session_factory, scoped_run):
    scope, run_id = scoped_run
    with pytest.raises(RuntimeError), session_factory.begin() as s:
        run = log.lock_run(s, scope, run_id)
        log.append(s, run, Event("run.status_changed", {"to": "READY"}))
        raise RuntimeError("boom")
    with session_factory() as s:
        run = repos.get_run(s, scope, run_id)
        assert run.status == "DRAFT" and run.next_seq == 1
        assert (
            s.scalar(
                select(func.count()).select_from(m.EventRow).where(m.EventRow.run_id == run_id)
            )
            == 0
        )


def test_concurrent_writers_never_gap_or_duplicate(session_factory, scoped_run):
    scope, run_id = scoped_run
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(10):
                with session_factory.begin() as s:
                    run = log.lock_run(s, scope, run_id)
                    log.append(s, run, Event("noop", {"w": n, "i": i}))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with session_factory() as s:
        seqs = list(
            s.scalars(
                select(m.EventRow.sequence)
                .where(m.EventRow.run_id == run_id)
                .order_by(m.EventRow.sequence)
            )
        )
    assert seqs == list(range(1, 61))


def test_reset_event_bumps_epoch_and_wipes_projection(session_factory, scoped_run):
    scope, run_id = scoped_run
    with session_factory.begin() as s:
        run = log.lock_run(s, scope, run_id)
        log.append(s, run, Event("clock.set", {"at": "2026-10-15T09:00:00Z", "initial": True}))
        log.append(s, run, Event("clock.advanced", {"to": "2026-10-15T09:30:00Z"}))
        s.add(m.Organization(run_id=run_id, id="org_1", name="o", domain="d", policies=[]))
        run.agent_actions = 3
        s.flush()
        log.append(s, run, Event("run.reset", {"epoch": 1}))
        assert run.epoch == 1 and run.agent_actions == 0
        assert run.virtual_time == run.start_time
        assert (
            s.scalar(
                select(func.count())
                .select_from(m.Organization)
                .where(m.Organization.run_id == run_id)
            )
            == 0
        )
    with session_factory() as s:
        last = (
            s.execute(
                select(m.EventRow)
                .where(m.EventRow.run_id == run_id)
                .order_by(m.EventRow.sequence.desc())
            )
            .scalars()
            .first()
        )
        assert last.type == "run.reset" and last.epoch == 1


def test_wrong_scope_cannot_lock_run(session_factory, scoped_run):
    from mailsim.domain.errors import NotFound

    _, run_id = scoped_run
    with session_factory() as s, pytest.raises(NotFound):
        log.lock_run(s, repos.Scope("ten_x", "prj_x"), run_id)


def test_id_generator_state_is_persisted_on_run(session_factory, scoped_run):
    scope, run_id = scoped_run
    with session_factory.begin() as s:
        run = log.lock_run(s, scope, run_id)
        gen = log.id_generator(run)
        first = gen.next("msg")
        log.save_ids(run, gen)
    with session_factory.begin() as s:
        run = log.lock_run(s, scope, run_id)
        gen2 = log.id_generator(run)
        assert run.id_counter == 1 and gen2.next("msg") != first
