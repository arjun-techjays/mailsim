"""Composition root for the REST API: ``python -m mailsim.serve bootstrap|serve``.

This module (top import layer, beside ``mailsim.cli``) is the one place that constructs the
engine; REST handlers only ever receive role facades through ``facade_for``.

Environment: ``MAILSIM_DATABASE_URL`` (default: the local Compose database),
``MAILSIM_BLOB_DIR`` (default ``var/blobs``), ``MAILSIM_MAX_BODY_BYTES`` (request size cap).

- ``bootstrap [--tenant NAME] [--project NAME] [--migrate] [--output FILE]`` creates a tenant
  + project with one controller and one evaluator key. Keys are shown once (only hashes are
  stored). Prefer ``--output`` (file written with mode 0600; stdout then carries ids only),
  and never run it where stdout is captured by logs or CI output.
- ``serve [--host 127.0.0.1] [--port 8000] [--local-no-auth]`` runs uvicorn on the app built
  from the environment and, unless ``--no-protocols``, the IMAP/SMTP listeners beside it on
  the same host (``--imap-port 1143 --smtp-port 1587``, ``--tls-self-signed`` /
  ``--tls-cert``/``--tls-key``; a non-loopback host needs TLS or ``--allow-insecure-remote``).
  Listeners that cannot start (port in use, ...) are reported and the API keeps serving. ``--local-no-auth`` (or ``MAILSIM_LOCAL_NO_AUTH=1``) is a LOCAL
  DEVELOPMENT convenience: key-less requests act as a controller+evaluator operator for one
  project (``MAILSIM_LOCAL_PROJECT_ID``, else the only project, else a new "local" project).
  It refuses to start unless bound to a loopback address. Agent interfaces (agent mailbox
  REST, Gmail, Graph, IMAP/SMTP, MCP agent tools) still require agent keys, and hidden
  answer keys stay unreachable for agents.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import inspect
import json
import logging
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from sqlalchemy import select

from mailsim.api.app import create_app
from mailsim.api.auth import bootstrap
from mailsim.api.idempotency import SWEEP_BATCH, sweep_expired
from mailsim.api.local_mode import LocalAccess, is_loopback, new_token
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import SimError
from mailsim.engine.core import SimEngine
from mailsim.llm.gateway import LLMGateway
from mailsim.llm.judge import LLMJudge
from mailsim.personas.runtime import PersonaRuntime
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory, run_migrations
from mailsim.store.models import Project

DEFAULT_BLOB_DIR = "var/blobs"
EVALUATION_SERVICE = "mailsim.evaluation.service"
# Provider-compatible HTTP adapters mounted next to /v1 when installed (module exposes `router`).
ADAPTER_ROUTERS = ("mailsim.adapters.gmail.api", "mailsim.adapters.graph.api", "mailsim.mcp.http")


def mount_adapters(app: FastAPI, modules: Sequence[str] = ADAPTER_ROUTERS) -> list[str]:
    """Include each installed adapter's APIRouter; missing modules are skipped."""
    mounted = []
    for name in modules:
        if importlib.util.find_spec(name) is None:
            continue
        app.include_router(importlib.import_module(name).router)
        mounted.append(name)
    return mounted


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


LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:5173"
TRUE = frozenset({"1", "true", "yes", "on"})
log = logging.getLogger("mailsim.serve")


def local_no_auth_requested(environ: Mapping[str, str]) -> bool:
    return environ.get("MAILSIM_LOCAL_NO_AUTH", "").strip().lower() in TRUE


def check_loopback(host: str) -> None:
    if host.strip("[]").lower() not in LOOPBACK:
        raise SystemExit(
            f"--local-no-auth refuses to bind to {host!r}: use 127.0.0.1, ::1 or localhost"
        )


def local_operator(session_factory: Any, environ: Mapping[str, str]) -> Principal:
    """The key-less local operator principal (controller + evaluator for one project)."""
    with session_factory.begin() as session:
        wanted = environ.get("MAILSIM_LOCAL_PROJECT_ID", "").strip()
        if wanted:
            project = session.get(Project, wanted)
            if project is None:
                raise SystemExit(f"MAILSIM_LOCAL_PROJECT_ID={wanted!r} does not exist")
        else:
            projects = session.execute(select(Project).order_by(Project.created_at)).scalars().all()
            if len(projects) > 1:
                raise SystemExit("several projects exist: set MAILSIM_LOCAL_PROJECT_ID")
            project = projects[0] if projects else None
            if project is None:
                created = bootstrap(session, "local", "local")  # keys are not printed
                project = session.get(Project, created["project_id"])
        assert project is not None
        return Principal(project.tenant_id, project.id, Role.CONTROLLER, key_id="local-operator",
                         local_operator=True)  # fmt: skip


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
    if local_no_auth_requested(env):
        access = LocalAccess(local_operator(session_factory, env), new_token())
        state["local_access"] = access
        warning = (
            "LOCAL NO-AUTH MODE: the dashboard needs no API key and acts as controller+evaluator "
            f"for project {access.operator.project_id}. Loopback clients only; never expose this "
            "server. Agent interfaces still require agent keys."
        )
        log.warning("!" * 20 + " " + warning + " " + "!" * 20)
        dashboard = env.get("MAILSIM_DASHBOARD_URL", DEFAULT_DASHBOARD_URL).rstrip("/")
        if not is_loopback(urlsplit(dashboard).hostname):
            print("WARNING: MAILSIM_DASHBOARD_URL is not a loopback URL; the launch token in the "
                  "link below is readable by that site", file=sys.stderr)  # fmt: skip
        # the launch token goes to the terminal only (never to the log): it is what separates
        # the developer's browser from other local processes, such as the agent under test
        print(f"WARNING: {warning}\nOpen the dashboard: {dashboard}/#local_token={access.token}",
              file=sys.stderr)  # fmt: skip
    return state


def app_from_env(environ: Mapping[str, str] | None = None) -> FastAPI:
    app = create_app(**build_state(environ))
    mount_adapters(app)
    return app


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
    sub.add_parser("sweep", help="delete expired idempotency records (also done on each claim)")
    pr = sub.add_parser("protocols", help="run ONLY the IMAP4rev1 + SMTP submission listeners "
                                          "(agent keys; loopback unless TLS)")  # fmt: skip
    pr.add_argument("--host", default="127.0.0.1")
    _protocol_arguments(pr)
    sub.add_parser("mcp-stdio", help="serve the MCP tools on stdin/stdout for one API key "
                                     "(MAILSIM_API_KEY; logs go to stderr)")  # fmt: skip
    srv = sub.add_parser("serve", help="run the HTTP API with uvicorn")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--local-no-auth", action="store_true",
                     help="LOCAL DEV ONLY: no key needed for the operator (loopback only)")  # fmt: skip
    srv.add_argument("--no-protocols", action="store_true",
                     help="do not start the IMAP/SMTP listeners with the API")  # fmt: skip
    _protocol_arguments(srv)
    return p


def _protocol_arguments(p: argparse.ArgumentParser) -> None:
    """IMAP/SMTP listener options shared by `serve` and `serve protocols`."""
    p.add_argument("--imap-port", type=int, default=1143)
    p.add_argument("--smtp-port", type=int, default=1587)
    p.add_argument("--tls-cert", type=Path, help="PEM certificate for STARTTLS")
    p.add_argument("--tls-key", type=Path, help="PEM private key (default: in --tls-cert)")
    p.add_argument("--tls-self-signed", action="store_true",
                   help="STARTTLS with an ephemeral self-signed certificate (local testing)")  # fmt: skip
    p.add_argument("--allow-insecure-remote", action="store_true",
                   help="allow a non-loopback IMAP/SMTP bind WITHOUT TLS (credentials in clear)")  # fmt: skip


@dataclass(frozen=True)
class ProtocolOptions:
    """IMAP/SMTP listener settings (`serve` starts them with the API; `serve protocols`)."""

    host: str = "127.0.0.1"
    imap_port: int = 1143
    smtp_port: int = 1587
    tls_cert: Path | None = None
    tls_key: Path | None = None
    tls_self_signed: bool = False
    allow_insecure_remote: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> ProtocolOptions:
        return cls(host=args.host, imap_port=args.imap_port, smtp_port=args.smtp_port,
                   tls_cert=args.tls_cert, tls_key=args.tls_key,
                   tls_self_signed=args.tls_self_signed,
                   allow_insecure_remote=args.allow_insecure_remote)  # fmt: skip

    def tls_context(self) -> Any:
        from mailsim.protocols import tls

        if self.tls_cert is not None:
            return tls.server_context(self.tls_cert, self.tls_key)
        if self.tls_self_signed:
            return tls.self_signed_context()
        return None


@contextlib.contextmanager
def protocol_listeners(app: FastAPI, options: ProtocolOptions | None) -> Iterator[Any]:
    """Run the IMAP/SMTP listeners beside the API for the duration of the block.

    The listeners (or None) are stored on ``app.state.protocols`` for ``/v1/system/status``
    and run connection details. Failing to start them (a non-loopback host without TLS, a
    port already in use, a bad certificate) is reported on stderr and the API keeps serving
    with the listeners marked not running. Listeners always require agent keys (local
    no-auth mode never applies to them)."""
    app.state.protocols = None
    if options is None:
        yield None
        return
    from mailsim.protocols.server import ProtocolServers

    hint = "the API keeps running; use --imap-port/--smtp-port, or --no-protocols"
    try:
        context = options.tls_context()
        servers = ProtocolServers(
            app.state.engine, host=options.host, imap_port=options.imap_port,
            smtp_port=options.smtp_port, tls=context is not None, tls_context=context,
            session_factory=app.state.session_factory,
            allow_insecure_remote=options.allow_insecure_remote,
        )  # fmt: skip
    except (ValueError, OSError) as exc:  # non-loopback without TLS, unreadable certificate
        print(f"IMAP/SMTP listeners not started: {exc} ({hint})", file=sys.stderr)
        yield None
        return
    app.state.protocols = servers
    try:
        servers.start()
    except OSError as exc:  # e.g. a port already in use
        print(f"IMAP/SMTP listeners not started: {exc} ({hint})", file=sys.stderr)
        yield servers
        return
    print(f"IMAP on {options.host}:{servers.imap_port}, SMTP on {options.host}:"
          f"{servers.smtp_port} (STARTTLS: {'yes' if servers.tls else 'no'}; user = mailbox "
          "address, password = agent key)", file=sys.stderr)  # fmt: skip
    try:
        yield servers
    finally:
        servers.stop()


def _serve(
    host: str, port: int, protocols: ProtocolOptions | None = None
) -> None:  # pragma: no cover - blocks
    import uvicorn

    app = app_from_env()
    with protocol_listeners(app, protocols):
        uvicorn.run(app, host=host, port=port)


def serve_command(host: str, port: int, local_no_auth: bool, serve: Callable[..., Any],
                  environ: dict[str, str] | None = None,
                  protocols: ProtocolOptions | None = None) -> int:  # fmt: skip
    """Run the API (and, with `protocols`, the IMAP/SMTP listeners beside it)."""
    env = os.environ if environ is None else environ
    if local_no_auth:
        env["MAILSIM_LOCAL_NO_AUTH"] = "1"
    if local_no_auth_requested(env):
        check_loopback(host)
    if protocols is None:
        serve(host, port)
    else:
        serve(host, port, protocols)
    return 0


def mcp_stdio_command(environ: Mapping[str, str] | None = None,
                      run: Callable[..., Any] | None = None) -> int:  # fmt: skip
    """`mcp-stdio`: MCP over stdin/stdout. The key comes from MAILSIM_API_KEY only (never argv);
    local no-auth mode never applies; stdout is the protocol channel, so nothing else is
    printed there."""
    from mailsim.mcp.stdio import StdioConfigError, run_stdio

    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if k != "MAILSIM_LOCAL_NO_AUTH"}  # fmt: skip
    state = build_state(env)
    try:
        (run or run_stdio)(state["engine"], session_factory=state["session_factory"],
                           evaluation_service=state["evaluation_service"],
                           persona_runtime_factory=state["persona_runtime_factory"],
                           environ=env)  # fmt: skip
    except StdioConfigError as exc:
        print(f"mcp-stdio: {exc}", file=sys.stderr)
        return 2
    finally:
        state["db_engine"].dispose()
    return 0


def protocols_command(args: argparse.Namespace, run: Callable[..., Any] | None = None,
                      environ: Mapping[str, str] | None = None) -> int:  # fmt: skip
    """`protocols`: IMAP + SMTP listeners over the agent facade (never local no-auth)."""
    from mailsim.protocols import server as protocols

    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if k != "MAILSIM_LOCAL_NO_AUTH"}  # fmt: skip
    context = ProtocolOptions.from_args(args).tls_context()
    state = build_state(env)
    try:
        (run or protocols.run)(state["engine"], args.host, imap_port=args.imap_port,
                               smtp_port=args.smtp_port, tls=context is not None,
                               tls_context=context,
                               allow_insecure_remote=args.allow_insecure_remote)  # fmt: skip
    except ValueError as exc:  # e.g. non-loopback bind without TLS
        print(f"protocols: {exc}", file=sys.stderr)
        return 2
    finally:
        state["db_engine"].dispose()
    return 0


def _write_secret(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)  # also when the file already existed
        fh.write(data)


def main(argv: Sequence[str] | None = None, *, serve: Callable[[str, int], Any] = _serve) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "serve":
        options = None if args.no_protocols else ProtocolOptions.from_args(args)
        return serve_command(args.host, args.port, args.local_no_auth, serve, protocols=options)
    if args.cmd == "mcp-stdio":
        return mcp_stdio_command()
    if args.cmd == "protocols":
        return protocols_command(args)
    if args.cmd == "sweep":
        state = build_state()
        try:
            total = 0
            while True:
                with state["session_factory"].begin() as session:
                    n = sweep_expired(session, datetime.now(UTC))
                total += n
                if n < SWEEP_BATCH:
                    break
            print(json.dumps({"swept": total}))
        finally:
            state["db_engine"].dispose()
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
