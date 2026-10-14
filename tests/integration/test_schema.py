import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.store import repos
from mailsim.store.models import EventRow, Run, ScenarioVersion

pytestmark = pytest.mark.db


def _run(session, tenant, project, rid="run_a"):
    run = Run(id=rid, tenant_id=tenant, project_id=project, status="DRAFT", seed=1)
    session.add(run)
    session.flush()
    return run


def test_events_are_append_only(session):
    t = repos.create_tenant(session, "t1")
    p = repos.create_project(session, t.id, "p1")
    run = _run(session, t.id, p.id, "run_ev1")
    session.add(
        EventRow(
            run_id=run.id, sequence=1, epoch=0, type="x", actor="sim", operation="x", payload={}
        )
    )
    session.flush()
    with pytest.raises(DBAPIError):
        session.execute(text("UPDATE events SET type='y' WHERE run_id=:r"), {"r": run.id})
    session.rollback()


def test_events_cannot_be_deleted(session):
    t = repos.create_tenant(session, "t1")
    p = repos.create_project(session, t.id, "p1")
    run = _run(session, t.id, p.id, "run_ev2")
    session.add(
        EventRow(
            run_id=run.id, sequence=1, epoch=0, type="x", actor="sim", operation="x", payload={}
        )
    )
    session.flush()
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM events WHERE run_id=:r"), {"r": run.id})
    session.rollback()


def test_event_sequence_unique(session):
    t = repos.create_tenant(session, "t1")
    p = repos.create_project(session, t.id, "p1")
    run = _run(session, t.id, p.id, "run_ev3")
    for _ in range(2):
        session.add(
            EventRow(
                run_id=run.id, sequence=1, epoch=0, type="x", actor="sim", operation="x", payload={}
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_scenario_versions_immutable(session):
    from mailsim.store.models import Scenario

    t = repos.create_tenant(session, "t1")
    p = repos.create_project(session, t.id, "p1")
    session.add(Scenario(id="scn_imm", tenant_id=t.id, project_id=p.id, key="k"))
    session.flush()
    session.add(
        ScenarioVersion(
            id="sv_imm",
            scenario_id="scn_imm",
            version=1,
            spec={},
            spec_hash="h",
            fixtures={},
            seed=1,
        )
    )
    session.flush()
    with pytest.raises(DBAPIError):
        session.execute(text("UPDATE scenario_versions SET spec='{}' WHERE id='sv_imm'"))
    session.rollback()


def test_scope_isolation(session):
    t1 = repos.create_tenant(session, "t1")
    t2 = repos.create_tenant(session, "t2")
    p1 = repos.create_project(session, t1.id, "p1")
    p2 = repos.create_project(session, t2.id, "p2")
    run = _run(session, t1.id, p1.id, "run_scope")
    own = repos.Scope(t1.id, p1.id)
    other = repos.Scope(t2.id, p2.id)
    assert repos.get_run(session, own, run.id).id == run.id
    assert [r.id for r in repos.list_runs(session, own)] == [run.id]
    assert repos.list_runs(session, other) == []
    with pytest.raises(NotFound):
        repos.get_run(session, other, run.id)
    with pytest.raises(NotFound):
        repos.get_project(session, repos.Scope(t2.id, p1.id))
    assert repos.get_project(session, own).id == p1.id
    assert repos.get_run(session, own, run.id, lock=True).id == run.id
    assert repos.as_dict(p1, ["name"]) == {"name": "p1"}


def test_repo_validation(session):
    with pytest.raises(InvalidRequest):
        repos.create_tenant(session, " ")
    t = repos.create_tenant(session, "t")
    with pytest.raises(InvalidRequest):
        repos.create_project(session, t.id, "")
    with pytest.raises(NotFound):
        repos.create_project(session, "ten_missing", "x")


def test_db_helpers(db_engine, monkeypatch):
    from mailsim.store import db

    monkeypatch.setenv("MAILSIM_DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    assert db.database_url() == "postgresql+psycopg://u:p@h/d"
    monkeypatch.delenv("MAILSIM_DATABASE_URL")
    assert db.database_url() == db.DEFAULT_URL
    engine = db.make_engine(db_engine.url.render_as_string(hide_password=False))
    with db.make_session_factory(engine)() as s:
        assert s.execute(text("select 1")).scalar() == 1
    engine.dispose()
