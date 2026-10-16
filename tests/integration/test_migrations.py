import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import MetaData

from mailsim.store.db import run_migrations
from mailsim.store.models import Base
from tests.conftest import ADMIN_URL, TEST_DB, scratch_database

pytestmark = pytest.mark.db


def test_upgrade_downgrade_upgrade_and_no_drift():
    with scratch_database("migration") as url:
        run_migrations(url)
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
            assert tables >= set(Base.metadata.tables) | {"alembic_version"}
            run_migrations(url, "base")
            assert set(inspect(engine).get_table_names()) == {"alembic_version"}
            run_migrations(url)
            # drift check: migrated schema == models
            from alembic.autogenerate import compare_metadata
            from alembic.migration import MigrationContext

            with engine.connect() as conn:
                diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert diff == []
        finally:
            engine.dispose()
    _ = MetaData


def _exists(name: str) -> bool:
    admin = create_engine(ADMIN_URL)
    try:
        with admin.connect() as conn:
            found = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
            return found.first() is not None
    finally:
        admin.dispose()


def test_scratch_databases_are_dropped_even_when_the_test_fails():
    name = f"{TEST_DB}_probe"
    with pytest.raises(RuntimeError), scratch_database("probe") as url:
        assert url.endswith("/" + name) and _exists(name)
        raise RuntimeError("test body failed")
    assert not _exists(name)
