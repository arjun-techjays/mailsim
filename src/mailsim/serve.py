"""Composition root for the REST API: ``python -m mailsim.serve bootstrap|serve``.

This module (top import layer, beside ``mailsim.cli``) is the one place that constructs the
engine; REST handlers only ever receive role facades through ``facade_for``.

Environment: ``MAILSIM_DATABASE_URL`` (default: the local Compose database),
``MAILSIM_BLOB_DIR`` (default ``var/blobs``), ``MAILSIM_MAX_BODY_BYTES`` (request size cap).

- ``bootstrap [--tenant NAME] [--project NAME] [--migrate] [--output FILE]`` creates a tenant
  + project with one controller and one evaluator key. Keys are shown once (only hashes are
  stored). Prefer ``--output`` (file written with mode 0600; stdout then carries ids only),
  and never run it where stdout is captured by logs or CI output.
- ``serve [--host 127.0.0.1] [--port 8000]`` runs uvicorn on the app built from the
  environment.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from mailsim.api.app import create_app
from mailsim.api.auth import bootstrap
from mailsim.domain.errors import SimError
from mailsim.engine.core import SimEngine
from mailsim.llm.gateway import LLMGateway
from mailsim.llm.judge import LLMJudge
from mailsim.personas.runtime import PersonaRuntime
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory, run_migrations

DEFAULT_BLOB_DIR = "var/blobs"
EVALUATION_SERVICE = "mailsim.evaluation.service"


def evaluation_service(**deps: Any) -> Any | None:
    """WS-B's EvaluationService, or None when that module is not installed (routes: 501).

    Only the constructor arguments it declares (by name) are passed from `deps`. If the
    module exists but cannot be imported or constructed, this raises (fail fast).
    """
    if importlib.util.find_spec(EVALUATION_SERVICE) is None:
        return None
    cls = importlib.import_module(EVALUATION_SERVICE).EvaluationService
    params = inspect.signature(cls).parameters
    return cls(**{k: v for k, v in deps.items() if k in params})


def build_state(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    db = make_engine(env.get("MAILSIM_DATABASE_URL") or None)
    session_factory = make_session_factory(db)
    blobs = FilesystemBlobStore(Path(env.get("MAILSIM_BLOB_DIR", DEFAULT_BLOB_DIR)))
    state: dict[str, Any] = {"db_engine": db, "session_factory": session_factory, "blobs": blobs,
                             "engine": SimEngine(session_factory, blobs)}  # fmt: skip
    if "MAILSIM_MAX_BODY_BYTES" in env:
        state["max_body_bytes"] = int(env["MAILSIM_MAX_BODY_BYTES"])
    # LLM gateway (stub by default; see mailsim.llm.config for providers and budgets): it
    # backs the semantic judge (bound to the evaluated run) and the persona runtime.
    gateway = LLMGateway.from_config(session_factory=session_factory)
    state["llm_gateway"] = gateway
    state["persona_runtime_factory"] = lambda ctl: PersonaRuntime(ctl, gateway)
    state["evaluation_service"] = evaluation_service(
        **state, judge_factory=lambda run_id: LLMJudge(gateway, run_id=run_id)
    )
    return state


def app_from_env(environ: Mapping[str, str] | None = None) -> FastAPI:
    return create_app(**build_state(environ))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mailsim.serve",
                                description="mailsim REST API (sandbox; no external delivery)")  # fmt: skip
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser(
        "bootstrap",
        help="create a tenant+project and its controller/evaluator keys "
        "(do not run with logged stdout; prefer --output)",
    )
    b.add_argument("--tenant", default="default")
    b.add_argument("--project", default="default")
    b.add_argument("--migrate", action="store_true", help="apply migrations first")
    b.add_argument("--output", type=Path, help="write the keys to this file (mode 0600)")
    srv = sub.add_parser("serve", help="run the HTTP API with uvicorn")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    return p


def _serve(host: str, port: int) -> None:  # pragma: no cover - blocks; exercised manually
    import uvicorn

    uvicorn.run(app_from_env(), host=host, port=port)


def _write_secret(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)  # also when the file already existed
        fh.write(data)


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
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        _write_secret(args.output, text)
        ids = {k: out[k] for k in ("tenant_id", "project_id")}
        print(json.dumps({**ids, "keys_written_to": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
