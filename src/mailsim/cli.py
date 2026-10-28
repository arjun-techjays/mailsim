"""`sim` command line: validate, publish, run, reset, replay and inspect scenarios.

Talks to the engine directly (no HTTP client: `publish --api-key-env` runs the API routes
in-process). All output is JSON on stdout so it can be used from CI; errors are JSON on stderr
with exit code 2 (replay mismatch: 3; `bench run` / `bench compare` gate failed: 1). Keys are
read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import select

from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.engine.context import use_interface
from mailsim.engine.core import SimEngine
from mailsim.scenario.answer_keys import create_answer_key_version, load_answer_key
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import repos
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, make_session_factory, run_migrations
from mailsim.store.models import Project


def _emit(obj: Any, stream: Any = None) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str), file=stream or sys.stdout)


_ID_KEY_HELP = ("key file for hashed identifiers and the deny list (default: the committed "
                "src/mailsim/importers/data/enron-id.key); never created, a different key is "
                "refused: see docs/leadership-inboxes.md")  # fmt: skip


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sim", description="Email agent testing simulator (Phase 1)")
    p.add_argument("--blob-dir", default=os.environ.get("MAILSIM_BLOB_DIR", "var/blobs"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("db-upgrade", help="apply database migrations")
    sub.add_parser("project-create", help="create a tenant + project, print their ids")

    v = sub.add_parser("validate", help="validate a scenario file (no database needed)")
    v.add_argument("path", type=Path)

    pub = sub.add_parser("publish", help="store a scenario (file or pack directory) as an "
                         "immutable version, optionally with its hidden answer key")  # fmt: skip
    pub.add_argument("path", type=Path,
                     help="scenario.yaml / .json, or a pack directory that contains one")  # fmt: skip
    pub.add_argument("--answer-key", type=Path, default=None,
                     help="also publish this answer key: in the same transaction (direct mode), or "
                     "right after the scenario (--api-key-env)")  # fmt: skip
    _target_arguments(pub)

    ak = sub.add_parser("answer-key", help="hidden answer keys (evaluator-only content)")
    aksub = ak.add_subparsers(dest="answer_key_cmd", required=True)
    akp = aksub.add_parser("publish", help="publish an answer-key file for a published scenario")
    akp.add_argument("path", type=Path)
    _target_arguments(akp)

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
    en.add_argument("--id-key", type=Path, default=None, help=_ID_KEY_HELP)
    sc = isub.add_parser("scan", help="scan files/directories for residual real PII")
    sc.add_argument("paths", type=Path, nargs="+")
    sc.add_argument("--names", type=Path, default=None, help="registry JSON from import enron")
    sc.add_argument("--id-key", type=Path, default=None, help=_ID_KEY_HELP)

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
    ld.add_argument("--id-key", type=Path, default=None, help=_ID_KEY_HELP)
    names = ld.add_mutually_exclusive_group(required=True)
    names.add_argument("--names", type=Path, help="registry JSON from import enron (required for "
                       "the PII guarantee: every rendered message is scanned against it)")  # fmt: skip
    names.add_argument("--no-names", action="store_true",
                       help="scan only against the built-in lists (weaker; prints a warning)")  # fmt: skip

    _bench_parser(sub)

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


def _bench_parser(sub: Any) -> None:
    bench = sub.add_parser("bench", help="CI regression suite: run an agent build over fixed "
                           "scenario packs (repeated trials), compare two builds")  # fmt: skip
    bsub = bench.add_subparsers(dest="bench_cmd", required=True)
    br = bsub.add_parser("run", help="run every scenario pack against one agent build and "
                         "gate on the pass rate (exit 0 pass, 1 gate failed, 2 error)")  # fmt: skip
    br.add_argument("suite", type=Path, nargs="+",
                    help="scenario files, pack directories, or directories of packs")  # fmt: skip
    br.add_argument("--build", required=True, help="agent build id recorded on every run")
    agent = br.add_mutually_exclusive_group(required=True)
    agent.add_argument("--agent-cmd", help="command that runs the agent (no shell; it gets "
                       "MAILSIM_API_KEY, MAILSIM_RUN_ID, MAILSIM_MAILBOX_ID, ...)")  # fmt: skip
    agent.add_argument("--agent-callable", metavar="MODULE:FUNCTION",
                       help="in-process Python agent called with an AgentContext")  # fmt: skip
    br.add_argument("--pass-env", action="append", default=[], metavar="VAR",
                    help="also pass this environment variable to --agent-cmd (repeatable)")  # fmt: skip
    br.add_argument("--agent-cwd", type=Path, default=None, help="working directory of the agent")
    br.add_argument("--agent-model", default=None,
                    help="the agent's own LLM provider/model/version, recorded as given")  # fmt: skip
    br.add_argument("--api-url", default=None,
                    help="base URL of a running `serve`, given to the agent as MAILSIM_API_URL")  # fmt: skip
    br.add_argument("--trials", type=_positive, default=1, help="runs per scenario (>= 1)")
    br.add_argument("--rounds", type=_positive, default=1,
                    help="agent invocations per trial, each followed by --advance-seconds")  # fmt: skip
    br.add_argument("--advance-seconds", type=_non_negative, default=0.0,
                    help="virtual seconds to advance after each round (personas reply, events "
                    "fire)")  # fmt: skip
    br.add_argument("--timeout", type=_positive_float, default=600.0,
                    help="wall-clock seconds per --agent-cmd round")  # fmt: skip
    br.add_argument("--checks", choices=("on", "off"), default=None,
                    help="hidden deterministic checks (default: the answer key's checks_default)")  # fmt: skip
    br.add_argument("--min-pass-rate", type=_rate, default=1.0,
                    help="gate: every scenario's pass rate must reach this (0..1, default 1)")  # fmt: skip
    br.add_argument("--max-variance", type=_non_negative, default=None,
                    help="gate: every scenario's accuracy variance must not exceed this")  # fmt: skip
    br.add_argument("--json", dest="json_out", type=Path, default=None,
                    help="also write the result document here")  # fmt: skip
    br.add_argument("--junit", type=Path, default=None, help="write a JUnit XML report here")
    br.add_argument("--project", default=None,
                    help="an existing project id (default: MAILSIM_LOCAL_PROJECT_ID, else the only "
                    "project in the database)")  # fmt: skip
    bc = bsub.add_parser("compare", help="compare two `bench run --json` results (no database); "
                         "exit 1 when the candidate regressed")  # fmt: skip
    bc.add_argument("baseline", type=Path)
    bc.add_argument("candidate", type=Path)
    bc.add_argument("--tolerance", type=_rate, default=0.0,
                    help="allowed drop of a pass rate / mean accuracy (0..1, default 0)")  # fmt: skip
    bc.add_argument("--fail-on-missing", action="store_true",
                    help="also fail when a baseline scenario is missing from the candidate")  # fmt: skip
    bc.add_argument("--json", dest="json_out", type=Path, default=None,
                    help="also write the comparison here")  # fmt: skip


def _target_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", default=None,
                   help="an existing project id (default: MAILSIM_LOCAL_PROJECT_ID, else the only "
                   "project in the database)")  # fmt: skip
    p.add_argument("--api-key-env", metavar="VAR", default=None,
                   help="go through the API routes (roles, validation, idempotency) as the key "
                   "stored in environment variable VAR, against the database the server uses "
                   "(MAILSIM_DATABASE_URL); the key is never printed")  # fmt: skip


class ApiFailure(Exception):
    """An error answer of an in-process API call (the frozen error body, printed as is)."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", "")))
        self.error = error


def _scenario_file(path: Path) -> Path:
    """A scenario file, or the scenario document of a pack directory."""
    if not path.is_dir():
        return path
    for name in ("scenario.yaml", "scenario.yml", "scenario.json"):
        if (path / name).is_file():
            return path / name
    raise InvalidRequest(f"no scenario.yaml or scenario.json in {path}")


def _default_scope(session_factory: Any, wanted: str | None) -> repos.Scope:
    """`--project`, else MAILSIM_LOCAL_PROJECT_ID, else the only project (as the local no-auth
    dashboard resolves it): publishing never creates a project."""
    wanted = wanted or os.environ.get("MAILSIM_LOCAL_PROJECT_ID", "").strip() or None
    if wanted:
        return _scope(session_factory, wanted)
    with session_factory() as s:
        found = s.execute(select(Project).order_by(Project.created_at).limit(2)).scalars().all()
    if len(found) != 1:
        raise InvalidRequest("several projects exist: pass --project <id>" if found else
                             "no project exists: create one (sim project-create) or pass --project")  # fmt: skip
    return repos.Scope(found[0].tenant_id, found[0].id)


def _key_out(key_id: str, scenario_key: str, version: int,
             scenario_versions: list[int] | None) -> dict[str, Any]:  # fmt: skip
    """Answer-key metadata for the output (never assertion content)."""
    return {"answer_key_id": key_id, "scenario_key": scenario_key, "version": version,
            "scenario_versions": scenario_versions}  # fmt: skip


def _with_key(out: dict[str, Any], key_out: dict[str, Any], scenario_too: bool) -> dict[str, Any]:
    """`answer-key publish` prints the key's fields; `publish --answer-key` nests them."""
    if scenario_too:
        out["answer_key"] = key_out
    else:
        out.update(key_out)
    return out


def _publish(args: argparse.Namespace, sf: Any, blobs: Any, sim: SimEngine) -> dict[str, Any]:
    """`publish` / `answer-key publish`: directly into a project, or through the API routes."""
    if args.cmd == "publish":
        loaded = load_scenario(_scenario_file(args.path))
        key_path = args.answer_key
    else:
        loaded, key_path = None, args.path
    key_doc = load_answer_key(key_path) if key_path is not None else None
    if loaded is not None and key_doc is not None and key_doc.answer_key.scenario != loaded.key:
        raise InvalidRequest(f"answer key is for {key_doc.answer_key.scenario!r}, not "
                             f"{loaded.key!r}")  # fmt: skip
    if args.api_key_env:
        return _publish_via_api(args, sf, blobs, sim, loaded, key_path, key_doc)
    scope = _default_scope(sf, args.project)
    out: dict[str, Any] = {"project_id": scope.project_id}
    with sf.begin() as s:  # direct mode: scenario and answer key are stored both or neither
        if loaded is not None:
            row = create_scenario_version(s, scope, loaded, blobs)
            out.update(scenario_key=loaded.key, scenario_version_id=row.id, version=row.version,
                       spec_hash=row.spec_hash)  # fmt: skip
        if key_doc is not None:
            k = create_answer_key_version(s, scope, key_doc, blobs)
            _with_key(out, _key_out(k.id, key_doc.answer_key.scenario, k.version,
                                    k.scenario_versions), loaded is not None)  # fmt: skip
    return out


def _publish_via_api(args: argparse.Namespace, sf: Any, blobs: Any, sim: SimEngine,
                     loaded: Any, key_path: Path | None, key_doc: Any) -> dict[str, Any]:  # fmt: skip
    """Through the API routes, in-process. Two requests (scenario, then key): when the key is
    refused, the error output names the scenario version that WAS published."""
    import asyncio

    from mailsim.api.app import create_app
    from mailsim.api.inprocess import asgi_request

    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise InvalidRequest(f"environment variable {args.api_key_env} is not set or empty")
    app = create_app(engine=sim, session_factory=sf, blobs=blobs)
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else b""
        resp = asyncio.run(asgi_request(app, method, path, headers=headers, body=data))
        try:
            if resp.truncated:
                raise ValueError("truncated")
            result = json.loads(resp.body or b"{}")
            if not isinstance(result, dict):
                raise ValueError("not an object")
        except ValueError:
            raise ApiFailure({"code": "INVALID_RESPONSE", "details": {"status": resp.status},
                              "message": f"{method} {path} answered HTTP {resp.status} without "
                                         "a JSON object"}) from None  # fmt: skip
        if resp.status >= 400:
            raise ApiFailure(result.get("error") or {"code": str(resp.status), "message": ""})
        return result

    me = call("GET", "/v1/me")
    if args.project and args.project != me["project_id"]:
        raise InvalidRequest(f"the key in {args.api_key_env} belongs to project "
                             f"{me['project_id']}, not {args.project}")  # fmt: skip
    out: dict[str, Any] = {"project_id": me["project_id"]}
    if loaded is not None:
        fixtures = {rel: base64.b64encode(raw).decode() for rel, raw in loaded.fixtures.items()}
        source = loaded.source.decode("utf-8")
        row = call("POST", "/v1/scenarios", {"source": source, "fixtures": fixtures})
        out.update(scenario_key=row["scenario_key"], scenario_version_id=row["id"],
                   version=row["version"], spec_hash=row["spec_hash"])  # fmt: skip
    if key_path is not None:
        scenario = key_doc.answer_key.scenario
        try:
            ak = call("POST", f"/v1/scenarios/{quote(scenario, safe='')}/answer-keys",
                      {"source": key_path.read_text(encoding="utf-8")})  # fmt: skip
        except ApiFailure as exc:
            if loaded is not None:  # the scenario is published; say so next to the key's error
                exc.error = {**exc.error, "published": dict(out)}
            raise
        _with_key(out, _key_out(ak["id"], ak["scenario_key"], ak["version"],
                                ak["scenario_versions"]), loaded is not None)  # fmt: skip
    return out


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
    except ApiFailure as exc:
        _emit(exc.error, sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        if args.cmd not in ("import", "generate", "bench"):
            raise
        _emit({"code": "INVALID_REQUEST", "message": str(exc)}, sys.stderr)
        return 2


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def _non_negative(value: str) -> float:
    x = float(value)
    if not (x >= 0 and x != float("inf")):
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return x


def _positive_float(value: str) -> float:
    x = _non_negative(value)
    if x == 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return x


def _rate(value: str) -> float:
    x = _non_negative(value)
    if x > 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return x


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


def _load_key(path: Path | None) -> bytes:
    """The id key (`enron.load_key`: the committed key unless `--id-key` names another file;
    never created, a missing or different key is an error)."""
    from mailsim.importers import enron

    return enron.load_key(path or enron.DEFAULT_ID_KEY)


def _import(args: argparse.Namespace) -> int:
    from mailsim.importers import enron
    from mailsim.importers.pii import Scanner
    from mailsim.importers.pseudonymize import DenyList

    if args.import_cmd == "scan":
        deny = DenyList(_load_key(args.id_key))
        findings = Scanner(_names(args.names), deny=deny).scan_paths(args.paths)
        _emit({"ok": not findings, "findings": len(findings),
               "by_kind": _count(f.kind for f in findings),
               "examples": [str(f) for f in findings[:50]]})  # fmt: skip
        return 0 if not findings else 1
    key = _load_key(args.id_key)
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

    key = _load_key(args.id_key)
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
        volume=volume, start_at=start, days=args.days, extra_names=_names(args.names), id_key=key,
    )  # fmt: skip
    scenarios = leadership.generate(header, records, opts)
    paths = leadership.write_scenarios(args.out, scenarios)
    _emit({"ok": True, "scenarios": [
        {"id": sc.scenario_id, "path": str(p), "messages": sc.messages, "threads": sc.threads,
         "topics": sc.topics, "skipped": sc.skipped} for sc, p in zip(scenarios, paths, strict=True)
    ]})  # fmt: skip
    return 0


def _write(path: Path | None, data: bytes) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _as_json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n").encode()


def _bench_run(args: argparse.Namespace, sf: Any, blobs: Any, sim: SimEngine) -> int:
    """`sim bench run`: publish the suite, run N trials per scenario, write JSON/JUnit, gate."""
    from mailsim.bench import report, suite
    from mailsim.bench.run import BenchConfig, BenchDeps, run_bench
    from mailsim.evaluation.service import EvaluationService
    from mailsim.llm.config import LLMConfig
    from mailsim.llm.gateway import LLMGateway
    from mailsim.llm.judge import LLMJudge
    from mailsim.personas.runtime import PersonaRuntime, advance_clock
    from mailsim.runner.command import CommandRunner
    from mailsim.runner.python import CallableRunner

    runner: CommandRunner | CallableRunner
    if args.agent_cmd is not None:
        runner = CommandRunner(args.agent_cmd, pass_env=args.pass_env, cwd=args.agent_cwd)
    else:
        try:
            runner = CallableRunner(args.agent_callable)
        except (ImportError, AttributeError) as exc:
            raise InvalidRequest(
                f"cannot load agent callable {args.agent_callable!r}: {exc}"
            ) from exc
    scope = _default_scope(sf, args.project)
    entries = suite.discover(args.suite)
    published = suite.publish(sf, scope, blobs, entries)
    llm_cfg = LLMConfig.from_env()
    gateway = LLMGateway.from_config(llm_cfg, session_factory=sf)
    deps = BenchDeps(
        engine=sim, session_factory=sf, scope=scope,
        evaluator=EvaluationService(sim, sf, blobs, judge_factory=lambda run_id: LLMJudge(
            gateway, run_id=run_id)),
        advance=lambda ctl, run_id, seconds: advance_clock(ctl, PersonaRuntime(ctl, gateway),
                                                           run_id, seconds),
        llm={"provider": llm_cfg.provider, "model": llm_cfg.model}, llm_usage=gateway.usage,
    )  # fmt: skip
    config = BenchConfig(
        build=args.build, trials=args.trials, rounds=args.rounds,
        advance_seconds=args.advance_seconds, timeout_seconds=args.timeout, checks=args.checks,
        agent_model=args.agent_model, min_pass_rate=args.min_pass_rate,
        max_variance=args.max_variance, api_url=args.api_url,
    )  # fmt: skip
    doc = run_bench(deps, runner, published, config)
    _write(args.json_out, _as_json(doc))
    _write(args.junit, report.render_junit(doc))
    _emit(doc)
    return 0 if doc["gate"]["passed"] else 1


def _bench_compare(args: argparse.Namespace) -> int:
    from mailsim.bench.compare import compare

    docs = []
    for path in (args.baseline, args.candidate):
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as exc:
            raise InvalidRequest(f"{path} is not JSON: {exc}") from exc
    result = compare(docs[0], docs[1], tolerance=args.tolerance,
                     fail_on_missing=args.fail_on_missing)  # fmt: skip
    _write(args.json_out, _as_json(result))
    _emit(result)
    return 0 if result["gate"]["passed"] else 1


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
    if args.cmd == "bench" and args.bench_cmd == "compare":
        return _bench_compare(args)

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
        if args.cmd in ("publish", "answer-key"):
            _emit(_publish(args, sf, blobs, sim))
            return 0
        if args.cmd == "bench":
            return _bench_run(args, sf, blobs, sim)
        scope = _scope(sf, args.project)

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
