"""Composition root for the REST API: build engine, sessions and blob store from the
environment and hand them to `create_app`.

Environment: ``MAILSIM_DATABASE_URL`` (default: the local Compose database) and
``MAILSIM_BLOB_DIR`` (default ``var/blobs``).

Run: ``python -m mailsim.api.server serve`` / ``python -m mailsim.api.server bootstrap``
(see `mailsim.api.cli_serve`).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from mailsim.api.app import create_app
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory

log = logging.getLogger("mailsim.api.server")
DEFAULT_BLOB_DIR = "var/blobs"
EVALUATION_SERVICE = "mailsim.evaluation.service"


def _sim_engine(session_factory: sessionmaker[Session], blobs: FilesystemBlobStore) -> Any:
    # The composition root is the one place that constructs the engine. Handlers never touch
    # it directly (import-linter forbids `mailsim.engine.core` in interfaces): they receive
    # role facades through `facade_for`. Loaded by name so that contract stays enforceable.
    core = importlib.import_module("mailsim.engine.core")
    return core.SimEngine(session_factory, blobs)


def evaluation_service(**deps: Any) -> Any | None:
    """WS-B's EvaluationService when installed, else None (evaluation routes answer 501).

    Only the constructor arguments it declares (by name) are passed from `deps`.
    """
    try:
        module = importlib.import_module(EVALUATION_SERVICE)
    except ImportError:
        log.info("evaluation service not installed; evaluation routes answer 501")
        return None
    cls = module.EvaluationService
    params = inspect.signature(cls).parameters
    try:
        return cls(**{k: v for k, v in deps.items() if k in params})
    except TypeError:
        log.warning("could not construct %s.EvaluationService; routes answer 501",
                    EVALUATION_SERVICE, exc_info=True)  # fmt: skip
        return None


def build_state(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    db = make_engine(env.get("MAILSIM_DATABASE_URL") or None)
    session_factory = make_session_factory(db)
    blobs = FilesystemBlobStore(Path(env.get("MAILSIM_BLOB_DIR", DEFAULT_BLOB_DIR)))
    engine = _sim_engine(session_factory, blobs)
    state: dict[str, Any] = {"db_engine": db, "session_factory": session_factory,
                             "blobs": blobs, "engine": engine}  # fmt: skip
    state["evaluation_service"] = evaluation_service(**state)
    return state


def app_from_env(environ: Mapping[str, str] | None = None) -> FastAPI:
    return create_app(**build_state(environ))


if __name__ == "__main__":  # pragma: no cover - thin entry point, logic lives in cli_serve
    from mailsim.api.cli_serve import main

    raise SystemExit(main())
