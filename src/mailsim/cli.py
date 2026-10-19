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

    imp = sub.add_parser("import", help="import a public email corpus (no database needed)")
    isub = imp.add_subparsers(dest="import_cmd", required=True)
    en = isub.add_parser("enron", help="sample, clean, pseudonymize and classify Enron bodies")
    en.add_argument("--parquet", type=Path, nargs="+", required=True, help="downloaded shards")
    en.add_argument("--limit", type=_positive, default=50_000, help="rows to sample (>= 1)")
    en.add_argument("--seed", type=int, default=7)
    en.add_argument("--out", type=Path, default=Path("var/enron/corpus.jsonl"))
    en.add_argument("--registry-out", type=Path, default=None,
                    help="also write harvested real names (for scanning; keep out of git)")  # fmt: skip
    en.add_argument("--keep-quotes", action="store_true", help="keep '>' quoted lines")
    en.add_argument("--sample-harvest", action="store_true",
                    help="harvest names from the sample only (faster, weaker)")  # fmt: skip
    en.add_argument("--id-key", type=Path, default=None,
                    help="secret key file for hashed identifiers (default: id.key next to --out; "
                         "created on first use; keep out of git)")  # fmt: skip
    sc = isub.add_parser("scan", help="scan files/directories for residual real PII")
    sc.add_argument("paths", type=Path, nargs="+")
    sc.add_argument("--names", type=Path, default=None, help="registry JSON from import enron")

    gen = sub.add_parser("generate", help="generate scenarios (no database needed)")
    gsub = gen.add_subparsers(dest="generate_cmd", required=True)
    ld = gsub.add_parser("leadership", help="leadership-team inbox scenarios from a corpus")
    ld.add_argument("--corpus", type=Path, required=True)
    ld.add_argument("--out", type=Path, default=Path("scenarios/leadership"))
    ld.add_argument("--seed", type=int, default=7)
    ld.add_argument("--roles", default="ceo,cfo,cbo,director_finance")
    ld.add_argument("--volume", default="", help="per-role message counts, e.g. ceo=40,cfo=30")
    ld.add_argument("--start-at", default="2026-10-16T08:00:00Z", help="virtual clock start")
    ld.add_argument("--days", type=_at_least_two, default=10,
                    help="days of mail before the clock start (>= 2)")  # fmt: skip
    names = ld.add_mutually_exclusive_group(required=True)
    names.add_argument("--names", type=Path, help="registry JSON from import enron (required for "
                       "the PII guarantee: every rendered message is scanned against it)")  # fmt: skip
    names.add_argument("--no-names", action="store_true",
                       help="scan only against the built-in lists (weaker; prints a warning)")  # fmt: skip

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
    except (ValueError, OSError) as exc:
        if args.cmd not in ("import", "generate"):
            raise
        _emit({"code": "INVALID_REQUEST", "message": str(exc)}, sys.stderr)
        return 2


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def _at_least_two(value: str) -> int:
    n = int(value)
    if n < 2:
        raise argparse.ArgumentTypeError("must be >= 2")
    return n


def _names(path: Path | None) -> list[str]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(n) for n in data.get("names", [])]


def _import(args: argparse.Namespace) -> int:
    from mailsim.importers import enron
    from mailsim.importers.pii import Scanner

    if args.import_cmd == "scan":
        findings = Scanner(_names(args.names)).scan_paths(args.paths)
        _emit({"ok": not findings, "findings": len(findings),
               "by_kind": _count(f.kind for f in findings),
               "examples": [str(f) for f in findings[:50]]})  # fmt: skip
        return 0 if not findings else 1
    key = enron.load_or_create_key(args.id_key or args.out.with_name("id.key"))
    cfg = enron.ImportConfig(limit=args.limit, seed=args.seed, strip_quotes=not args.keep_quotes,
                             harvest_all=not args.sample_harvest, id_key=key)  # fmt: skip
    try:
        records, header, registry = enron.import_enron(list(args.parquet), cfg)
    except enron.MissingDependency as exc:
        _emit({"code": "MISSING_DEPENDENCY", "message": str(exc)}, sys.stderr)
        return 2
    digest = enron.write_corpus(args.out, records, header)
    if args.registry_out is not None:
        enron.write_registry(args.registry_out, registry)
    _emit({"ok": True, "out": str(args.out), "sha256": digest, **header["stats"]})
    return 0


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return dict(sorted(out.items()))


def _generate(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from mailsim.importers import enron, leadership

    if args.no_names:
        print("WARNING: --no-names: generated text is scanned against the built-in name lists "
              "only, not the registry of real names harvested by `sim import enron`. Do not "
              "commit this output.", file=sys.stderr)  # fmt: skip
    header, records = enron.read_corpus(args.corpus)
    volume: dict[str, int] = {}
    for item in filter(None, (v.strip() for v in args.volume.split(","))):
        role, _, count = item.partition("=")
        volume[role.strip()] = int(count)
    start = datetime.strptime(args.start_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    opts = leadership.GenerateOptions(
        seed=args.seed, roles=[r.strip() for r in args.roles.split(",") if r.strip()],
        volume=volume, start_at=start, days=args.days, extra_names=_names(args.names),
    )  # fmt: skip
    scenarios = leadership.generate(header, records, opts)
    paths = leadership.write_scenarios(args.out, scenarios)
    _emit({"ok": True, "scenarios": [
        {"id": sc.scenario_id, "path": str(p), "messages": sc.messages, "threads": sc.threads,
         "topics": sc.topics, "skipped": sc.skipped} for sc, p in zip(scenarios, paths, strict=True)
    ]})  # fmt: skip
    return 0


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

    if args.cmd == "import":
        return _import(args)
    if args.cmd == "generate":
        return _generate(args)

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
