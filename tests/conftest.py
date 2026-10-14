from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from mailsim.store.db import run_migrations

ADMIN_URL = os.environ.get(
    "MAILSIM_TEST_ADMIN_URL", "postgresql+psycopg://mailsim:mailsim@localhost:54329/postgres"
)
TEST_DB = "mailsim_test"


def _url(db: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + db


def recreate_database(name: str) -> str:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return _url(name)


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
