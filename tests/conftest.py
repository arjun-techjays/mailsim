from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from mailsim.engine.core import SimEngine
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import repos
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import run_migrations
from tests.scenarios import EXAMPLE

ADMIN_URL = os.environ.get(
    "MAILSIM_TEST_ADMIN_URL", "postgresql+psycopg://mailsim:mailsim@localhost:54329/postgres"
)
# Each parallel workstream sets MAILSIM_TEST_DB so suites never drop each other's database.
TEST_DB = os.environ.get("MAILSIM_TEST_DB", "mailsim_test")


def _url(db: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + db


def _test_db_name(name: str) -> str:
    """Only plain lowercase test database names are ever dropped or created."""
    if not re.fullmatch(r"[a-z0-9_]+", name) or "test" not in name:
        raise ValueError(f"refusing to manage database {name!r}: not a *test* database name")
    return name


def recreate_database(name: str) -> str:
    _test_db_name(name)
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return _url(name)


def drop_database(name: str) -> None:
    _test_db_name(name)
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@contextmanager
def scratch_database(suffix: str) -> Iterator[str]:
    """A fresh `<TEST_DB>_<suffix>` database for one test, dropped afterwards (every extra
    database lengthens PostgreSQL's crash-recovery sync and uses disk)."""
    name = f"{TEST_DB}_{suffix}"
    url = recreate_database(name)
    try:
        yield url
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = recreate_database(TEST_DB)
    run_migrations(url)
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s
        s.rollback()


@dataclass
class Env:
    engine: SimEngine
    scope: repos.Scope
    session_factory: object
    blobs: FilesystemBlobStore
    tmp_path: Path

    def version(self, path: Path = EXAMPLE) -> str:
        with self.session_factory.begin() as s:
            return create_scenario_version(s, self.scope, load_scenario(path), self.blobs).id

    def run(self, path: Path = EXAMPLE, *, start: bool = True) -> str:
        run_id = self.engine.create_run(self.scope, self.version(path))
        if start:
            self.engine.start_run(self.scope, run_id)
        return run_id

    def mbx(self, run_id: str, persona: str) -> str:
        return self.engine.mailbox_id_for(self.scope, run_id, persona)


@pytest.fixture()
def env(session_factory, tmp_path) -> Env:
    with session_factory.begin() as s:
        t = repos.create_tenant(s, "t")
        p = repos.create_project(s, t.id, "p")
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    return Env(
        SimEngine(session_factory, blobs), repos.Scope(t.id, p.id), session_factory, blobs, tmp_path
    )
