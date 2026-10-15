"""Test-only composition root for the stdio transport (``python -m tests.mcp.stdio_entry``):
builds the engine from ``MAILSIM_DATABASE_URL`` / ``MAILSIM_BLOB_DIR`` and serves the key in
``MAILSIM_API_KEY``. The production entry point is the integrator's
``python -m mailsim.serve mcp-stdio``."""

from __future__ import annotations

import os
from pathlib import Path

from mailsim.engine.core import SimEngine
from mailsim.mcp.stdio import run_stdio
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory


def main() -> None:
    db = make_engine(os.environ["MAILSIM_DATABASE_URL"])
    try:
        sf = make_session_factory(db)
        run_stdio(SimEngine(sf, FilesystemBlobStore(Path(os.environ["MAILSIM_BLOB_DIR"]))))
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
