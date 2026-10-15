"""`python -m mailsim.api.server <command>`: serve the REST API or bootstrap credentials.

- ``bootstrap [--tenant NAME] [--project NAME]`` creates a tenant + project and prints its
  controller and evaluator keys as JSON (shown once; only hashes are stored).
- ``serve [--host 127.0.0.1] [--port 8000]`` runs uvicorn on the app built from the
  environment (``MAILSIM_DATABASE_URL``, ``MAILSIM_BLOB_DIR``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from mailsim.api.auth import bootstrap
from mailsim.api.server import app_from_env, build_state
from mailsim.domain.errors import SimError
from mailsim.store.db import run_migrations


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mailsim.api.server",
                                description="mailsim REST API (sandbox; no external delivery)")  # fmt: skip
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser(
        "bootstrap", help="create a tenant+project and print controller/evaluator keys"
    )
    b.add_argument("--tenant", default="default")
    b.add_argument("--project", default="default")
    b.add_argument("--migrate", action="store_true", help="apply migrations first")
    srv = sub.add_parser("serve", help="run the HTTP API with uvicorn")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    return p


def _serve(host: str, port: int) -> None:  # pragma: no cover - blocks; exercised manually
    import uvicorn

    uvicorn.run(app_from_env(), host=host, port=port)


def main(argv: Sequence[str] | None = None, *, serve: Callable[[str, int], Any] = _serve) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "serve":
        serve(args.host, args.port)
        return 0
    if args.migrate:
        run_migrations()
    state = build_state()
    try:
        with state["session_factory"].begin() as session:
            out = bootstrap(session, args.tenant, args.project)
    except SimError as exc:
        print(json.dumps({"error": exc.to_dict()}), file=sys.stderr)
        return 2
    finally:
        state["db_engine"].dispose()
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0
