"""`sim` command line: validate, publish, run, reset, replay and inspect scenarios.

Talks to the engine directly (no HTTP in Phase 1). All output is JSON on stdout so it can
be used from CI; errors are JSON on stderr with exit code 2 (replay mismatch: 3).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.engine.context import use_interface
from mailsim.engine.core import SimEngine
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import repos
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory, run_migrations
from mailsim.store.models import Project


def _emit(obj: Any, stream: Any = None) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str), file=stream or sys.stdout)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sim", description="Email agent testing simulator (Phase 1)")
    p.add_argument("--blob-dir", default=os.environ.get("MAILSIM_BLOB_DIR", "var/blobs"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("db-upgrade", help="apply database migrations")
    sub.add_parser("project-create", help="create a tenant + project, print their ids")

    v = sub.add_parser("validate", help="validate a scenario file (no database needed)")
    v.add_argument("path", type=Path)

    pub = sub.add_parser("publish", help="store a scenario as an immutable version")
    pub.add_argument("path", type=Path)
    pub.add_argument("--project", required=True)

    for name, help_ in (
        ("run-create", "create+seed a run from a scenario version id"),
        ("run-start", "start a READY run"),
        ("run-cancel", "cancel a run"),
        ("run-reset", "reset a run to its baseline (new epoch)"),
        ("run-replay", "replay a run from its event log and verify the state hash"),
        ("run-state", "dump canonical mailbox state"),
        ("run-events", "dump the event log"),
        ("run-actions", "dump the audit action log"),
        ("run-advance", "advance virtual time and fire due events"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("target", help="scenario version id (run-create) or run id")
        sp.add_argument("--project", required=True)
        if name == "run-advance":
            sp.add_argument("--seconds", type=float, required=True)
    return p


def _scope(session_factory: Any, project_id: str) -> repos.Scope:
    with session_factory() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise InvalidRequest(f"project {project_id} not found")
        return repos.Scope(project.tenant_id, project.id)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with use_interface("cli"):
            return _dispatch(args)
    except SimError as exc:
        _emit(exc.to_dict(), sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "validate":
        try:
            loaded = load_scenario(args.path)
        except InvalidRequest as exc:
            _emit(exc.to_dict(), sys.stderr)
            return 2
        _emit({"ok": True, "id": loaded.key, "version": loaded.version, "spec_hash": loaded.spec_hash,
               "fixtures": sorted(loaded.fixtures)})  # fmt: skip
        return 0

    if args.cmd == "db-upgrade":
        run_migrations()
        _emit({"ok": True})
        return 0

    engine_db = make_engine()
    sf = make_session_factory(engine_db)
    try:
        if args.cmd == "project-create":
            with sf.begin() as s:
                tenant = repos.create_tenant(s, "default")
                project = repos.create_project(s, tenant.id, "default")
                _emit({"tenant_id": tenant.id, "project_id": project.id})
            return 0

        blobs = FilesystemBlobStore(Path(args.blob_dir))
        sim = SimEngine(sf, blobs)
        scope = _scope(sf, args.project)

        if args.cmd == "publish":
            with sf.begin() as s:
                row = create_scenario_version(s, scope, load_scenario(args.path), blobs)
                _emit(
                    {
                        "scenario_version_id": row.id,
                        "version": row.version,
                        "spec_hash": row.spec_hash,
                    }
                )
            return 0
        if args.cmd == "run-create":
            _emit({"run_id": sim.create_run(scope, args.target)})
            return 0
        run = args.target
        if args.cmd == "run-start":
            sim.start_run(scope, run)
        elif args.cmd == "run-cancel":
            sim.cancel_run(scope, run)
        elif args.cmd == "run-reset":
            _emit({"epoch": sim.reset_run(scope, run)})
            return 0
        elif args.cmd == "run-advance":
            _emit(sim.advance(scope, run, args.seconds))
            return 0
        elif args.cmd == "run-replay":
            result = sim.replay_run(scope, run)
            _emit({"matches": result.matches, "replay_run_id": result.replay_run_id,
                   "original_hash": result.original_hash, "replay_hash": result.replay_hash,
                   "event_count": result.event_count, "missing_blobs": result.missing_blobs})  # fmt: skip
            return 0 if result.matches else 3
        elif args.cmd == "run-state":
            _emit(sim.state(scope, run))
            return 0
        elif args.cmd == "run-events":
            _emit(sim.events(scope, run))
            return 0
        elif args.cmd == "run-actions":
            _emit(sim.actions(scope, run))
            return 0
        _emit(sim.run_info(scope, run))
        return 0
    finally:
        engine_db.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
