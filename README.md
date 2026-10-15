# mailsim

Deterministic, provider-neutral email simulation engine for testing AI agents.
Implements **Phase 1 (core simulation engine)** of
[`email-agent-testing-simulator-spec.md`](email-agent-testing-simulator-spec.md).

> Status: Phase 1 only. There is **no** REST API, dashboard, evaluator, Gmail/Graph/IMAP/SMTP
> adapter, MCP server or LLM integration yet (spec Phases 2-5). The scenario schema and the
> compatibility contracts are proposals from the spec, not a claim of provider fidelity.

## Quick start

```bash
uv sync
docker compose up -d postgres        # PostgreSQL 16 on 127.0.0.1:54329
uv run sim db-upgrade
uv run sim validate scenarios/finance.invoice-bank-change/scenario.yaml
P=$(uv run sim project-create | python -c 'import json,sys;print(json.load(sys.stdin)["project_id"])')
V=$(uv run sim publish scenarios/finance.invoice-bank-change/scenario.yaml --project $P | python -c 'import json,sys;print(json.load(sys.stdin)["scenario_version_id"])')
R=$(uv run sim run-create $V --project $P | python -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
uv run sim run-start $R --project $P
uv run sim run-advance $R --seconds 600 --project $P
uv run sim run-reset $R --project $P
uv run sim run-replay $R --project $P     # exit 0 only if replayed state hash matches
```

## Architecture (Phase 1)

```
cli -> engine -> scenario -> store -> (mime | threads | providers) -> domain
```

* **Event-sourced.** `events` is append-only (DB trigger). Mailbox tables are a projection written
  in the same transaction by `engine/projectors.py`. Payloads are self-contained (ids, timestamps,
  blob refs), so replay never regenerates anything.
* **Determinism.** Time is the virtual clock; ids come from a seeded generator whose counter is
  persisted on the run. Same seed + same commands = identical event log and state hash.
* **Concurrency rule.** All commands on a run serialise on a row lock of `runs`
  (`SELECT ... FOR UPDATE`); that lock holder allocates gapless sequence numbers.
* **Reset** = new epoch on the same run: a `run.reset` event wipes the projection and the
  baseline (seed) events are re-applied. History is kept.
* **Replay** = re-apply recorded events into a fresh run and compare state hashes; missing blob
  artifacts are reported, not regenerated.
* **No external delivery.** `DeliveryPolicy` is the only place recipients are resolved: simulated
  mailboxes, or the external mode `deny` (default, whole send rejected) / `sink` / `event`.
  There is no SMTP/socket/HTTP client in `src/` (ruff banned-api + AST test), tests patch sockets
  to prove none is opened, and the Compose `app` service sits on an `internal` network.
  **Gap:** the host-level egress block for real deployments is only verifiable once Phase 4
  adds listeners; Phase 1 proves the in-process guarantee and ships the deny-by-default network.
* **Tenancy.** Application-scoped (`Scope(tenant, project)` on every repository/engine call;
  out-of-scope resources are `NOT_FOUND`). Row-level security is deferred to Phase 2 with auth.

## Known limitations / deliberate deviations

* Databases created before migration 0004 must be recreated: earlier scenario versions still
  embed inline assertions and earlier events lack `id_counter_after` (no backfill is provided;
  no persistent data existed when Wave 0 landed).
* Inline-derived answer keys share the version sequence with file keys (auto-numbered); publish
  file keys with an explicit, higher version.

* Determinism is per event log: after a reset the id counter keeps counting, so agent-created ids
  differ from a fresh run's (baseline ids are re-applied unchanged). Replay reproduces the log exactly.
* Reset is allowed only from READY/RUNNING. Only agent WRITES consume the action budget
  (`max_agent_actions`), whether they succeed or fail; reads and polling (list, get, search,
  threads, change feed) are free but still recorded as actions for efficiency metrics.
* The audit action log records recipients, counts and sizes but not subjects, bcc or bodies.
* `actor` is a trusted-caller argument; Phase 2/3 must expose agent-facing facades that hard-code it.

See the PR notes: provider id mapping tables, PASSED/FAILED verdicts (need the Phase 2
evaluator), scheduled events support only `email.receive`, persona runtime is not implemented.
