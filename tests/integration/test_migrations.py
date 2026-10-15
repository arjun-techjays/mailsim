import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.schema import MetaData

from mailsim.store.db import run_migrations
from mailsim.store.models import Base
from tests.conftest import TEST_DB, recreate_database

pytestmark = pytest.mark.db


def test_upgrade_downgrade_upgrade_and_no_drift():
    url = recreate_database(f"{TEST_DB}_migration")
    run_migrations(url)
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables) | {"alembic_version"}
    run_migrations(url, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    run_migrations(url)
    # drift check: migrated schema == models
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    engine.dispose()
    _ = MetaData
