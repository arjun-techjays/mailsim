from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from mailsim.store.db import database_url
from mailsim.store.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or database_url()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
