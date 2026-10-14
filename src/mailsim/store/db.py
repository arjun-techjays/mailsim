"""Engine/session construction and migration helper."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://mailsim:mailsim@localhost:54329/mailsim"


def database_url() -> str:
    return os.environ.get("MAILSIM_DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), future=True, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def run_migrations(url: str | None = None, revision: str = "head") -> None:
    """Apply Alembic migrations programmatically (used by CLI and tests)."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or database_url()).replace("%", "%%"))
    if revision == "base":
        command.downgrade(cfg, "base")
    else:
        command.upgrade(cfg, revision)
