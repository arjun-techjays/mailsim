# mailsim

Deterministic, provider-neutral email simulation engine for testing AI agents.
Implements **Phase 1 (core simulation engine)** of
[`email-agent-testing-simulator-spec.md`](email-agent-testing-simulator-spec.md).

> Status: Phase 1 engine + Waves 1-2 of Phases 2-5: REST API `/v1`, hidden answer keys and the
> post-run evaluator, LLM gateway (stub / Claude / Ollama / Claude Code CLI) with persona runtime
> and judge, the operator dashboard (`web/`), and the agent interfaces: Gmail API v1 and Microsoft
> Graph v1.0 compatibility profiles, IMAP4rev1 + SMTP submission listeners and an MCP server.
> Not yet built: CI/benchmark command, hosted controls (Wave 3).

## Agent interfaces (all require the run's agent key)

| Interface | Start | Endpoint | Profile |
|---|---|---|---|
| REST `/v1` | `serve` | `http://127.0.0.1:8000/v1/mailboxes/{mailbox_id}/...` | `openapi/openapi.json` |
| Gmail API v1 | `serve` | `http://127.0.0.1:8000/gmail/v1/users/me/...` | [`docs/compat/gmail.md`](docs/compat/gmail.md) |
| Microsoft Graph v1.0 | `serve` | `http://127.0.0.1:8000/graph/v1.0/me/...` | [`docs/compat/graph.md`](docs/compat/graph.md) |
| MCP (streamable HTTP) | `serve` | `http://127.0.0.1:8000/mcp` | [`docs/compat/mcp.md`](docs/compat/mcp.md) |
| MCP (stdio) | `MAILSIM_API_KEY=... serve mcp-stdio` | stdin/stdout | [`docs/compat/mcp.md`](docs/compat/mcp.md) |
| IMAP4rev1 / SMTP | `serve` (or `serve protocols` alone) | `127.0.0.1:1143` / `127.0.0.1:1587` (user = mailbox address, password = agent key) | [`docs/compat/imap-smtp.md`](docs/compat/imap-smtp.md) |

`serve` is `uv run python -m mailsim.serve serve`: one command brings up the API and the
IMAP/SMTP listeners (`--no-protocols` to skip them, `--imap-port` / `--smtp-port` to move them).
The agent key comes from the run-creation response (shown once) or
`POST /v1/runs/{run_id}/agent-credentials` (controller key). Minting a key does not revoke the
run's other agent keys (one per interface is fine); the response carries `key_id`, and a key is
revoked only explicitly with `DELETE /v1/keys/{key_id}` (or when the run is cancelled or
completed). Agents download attachment bytes with
`GET /v1/mailboxes/{mailbox_id}/messages/{message_id}/attachments/{attachment_id}` and forward
with `POST /v1/mailboxes/{mailbox_id}/messages/{message_id}/forward`; operators download with
`GET /v1/runs/{run_id}/mailboxes/{mailbox_id}/messages/{message_id}/attachments/{attachment_id}`.
Agent-facing errors never mention the simulation: on the agent mailbox routes and every agent
interface the action budget error reads `QUOTA_EXCEEDED` / "action limit reached" and an
inactive run reads `RUN_NOT_ACTIVE` / "mailbox is not available" (operator routes keep
`SIMULATION_LIMIT_EXCEEDED` and the engine messages). The dashboard's run page shows every
endpoint for a run (`GET /v1/runs/{run_id}/connection`), tests a key through any interface
(`POST /v1/runs/{run_id}/connection-tests`) and shows which interface each action came through
(`GET /v1/runs/{run_id}/activity`).

## Run the API and dashboard locally

```bash
uv sync && docker compose up -d postgres
export MAILSIM_DATABASE_URL=postgresql+psycopg://mailsim:mailsim@localhost:54329/mailsim
export MAILSIM_BLOB_DIR=var/blobs
uv run python -m mailsim.serve bootstrap --migrate --output var/keys.json   # controller + evaluator keys (0600)
uv run python -m mailsim.serve serve --port 8000            # API :8000 + IMAP :1143 + SMTP :1587
# optional LLM: MAILSIM_LLM_PROVIDER=anthropic (+ANTHROPIC_API_KEY), openai_compatible (+MAILSIM_LLM_BASE_URL)
#               or claude_cli (your local Claude Code login, see below)
cd web && npm ci && MAILSIM_API_URL=http://127.0.0.1:8000 npm run dev       # dashboard on :5173 (proxies /v1)
```

Typical flow: publish a scenario (`POST /v1/scenarios`), publish its hidden answer key
(`POST /v1/scenarios/{key}/answer-keys`), create a run (returns an agent key bound to one mailbox;
give only that to the agent under test), drive time (`/clock/advance`, personas reply on each
step), then evaluate after the run (`POST /v1/runs/{id}/evaluations`, evaluator key) and compare
builds (`GET /v1/scenarios/{key}/accuracy`). The OpenAPI document is `openapi/openapi.json`.
LLM provider outages surface as `UPSTREAM_UNAVAILABLE` (HTTP 503, `details.retryable`).
Expired idempotency records are swept on each claim; `python -m mailsim.serve sweep` purges all.

## Local development: Claude Code CLI provider and no-auth dashboard

Both are **local-only conveniences, off by default**.

**`claude_cli` LLM provider.** This provider generates persona replies and judge verdicts through
your installed Claude Code CLI and its login (`claude` then `/login`). It does not use
`ANTHROPIC_API_KEY`.

```bash
export MAILSIM_LLM_PROVIDER=claude_cli
export MAILSIM_LLM_MODEL=sonnet          # optional; empty = the CLI's default model
export MAILSIM_CLAUDE_CLI=claude         # optional; executable name or path
export MAILSIM_LLM_TIMEOUT_SECONDS=120   # optional per-call timeout (default 60)
```

How it runs the CLI:

* **Command.** Each call runs
  `claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config`.
  * The prompt goes on stdin. The system prompt goes in a file passed with
    `--system-prompt-file`.
  * There is no shell, and the model name is validated.
  * It runs in an empty temporary directory and its own process group. On timeout, the whole
    group is killed.
  * `--bare` is deliberately not used, because it disables the login.
* **Environment.** The child gets an allowlisted environment: `PATH`, `HOME`, locale, temp and
  Claude config directories.
  * No `ANTHROPIC_*` variables, database URL or other secrets are passed.
  * `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is set from the request's (budget-capped) `max_tokens`.
  * `MAILSIM_LLM_EFFORT` does not apply.
* **Usage and cost.** Usage and cost come from the CLI's JSON (`usage`, `total_cost_usd`) and are
  recorded in `llm_calls`, for failed calls too. When the CLI reports no usage, tokens are
  estimated and the row is marked `usage_estimated`.
* **Replay.** Replay reuses recorded outputs and never calls the CLI.
* **Failures.** These map to `UPSTREAM_UNAVAILABLE` (HTTP 503):
  * CLI missing;
  * not logged in;
  * non-zero exit;
  * timeout (retryable);
  * overload (retryable).
* **Limitation.** User-level Claude Code settings and memory may still apply.
* **Live test.** Opt in with `MAILSIM_LIVE_CLAUDE_CLI=1 uv run pytest -m live_llm tests/llm/test_claude_cli.py`.

**Local no-auth dashboard.** In this mode the dashboard needs no API key. It acts as a combined
**controller + evaluator** operator for the local project. The project is resolved in this order:

1. `MAILSIM_LOCAL_PROJECT_ID`;
2. the only project in the database;
3. a `local` project, bootstrapped when the database is empty (no keys are printed).

```bash
uv run python -m mailsim.serve serve --host 127.0.0.1 --port 8000 --local-no-auth   # or MAILSIM_LOCAL_NO_AUTH=1
#   -> prints: Open the dashboard: http://127.0.0.1:5173/#local_token=<random token>
cd web && MAILSIM_API_URL=http://127.0.0.1:8000 npm run dev                      # then open that link
```

The server mints a random launch token at every start and prints it only to the terminal. It
goes in the URL fragment, which is never sent to a server. The dashboard reads it once, removes
it from the address bar and keeps it in the tab's session. Without the link, the Connect page
explains where to find it. Set `MAILSIM_DASHBOARD_URL` if the dashboard runs elsewhere.

The token is what separates your browser from other local processes, such as the agent under
test. An agent that drops its key gets 401, not operator access, so hidden answer keys stay out
of its reach. Web pages you visit can't use the token either: cross-site requests can't send it,
and DNS rebinding doesn't give a page the token.

Limit: the token does not protect against other processes running as **your OS user** that
can read the server's terminal or memory, or your browser history (which may keep the launch
URL). Run untrusted agents as a different user or against a key-mode server.

The mode's other safeguards:

* **Loopback only.** The server refuses to start the mode unless bound to `127.0.0.1`, `::1` or
  `localhost`. While the mode is on, every request from a non-loopback peer or with a
  non-loopback `Host` header gets 403, whatever launched the app.
* **Loud warning.** The server prints a warning at startup.
* **Agent interfaces still require agent keys.** This covers the REST mailbox routes and the
  Gmail, Graph, IMAP/SMTP and MCP adapters.
* **Keys are always verified.** Any request that carries an `Authorization` header is checked as
  a key, so invalid or wrong-scheme keys get 401.

## Quick start

Server, agent interfaces and dashboard (no API key needed in the browser; personas and the
judge through your local Claude Code login):

```bash
uv sync && docker compose up -d postgres
export MAILSIM_DATABASE_URL=postgresql+psycopg://mailsim:mailsim@localhost:54329/mailsim
uv run sim db-upgrade                                      # first time only
MAILSIM_LLM_PROVIDER=claude_cli uv run python -m mailsim.serve serve --local-no-auth
#   -> REST/Gmail/Graph/MCP on http://127.0.0.1:8000, IMAP 127.0.0.1:1143, SMTP 127.0.0.1:1587,
#      and the dashboard link (with its launch token) on stderr
cd web && npm ci && MAILSIM_API_URL=http://127.0.0.1:8000 npm run dev   # open the printed link
```

The engine from the command line (no server):

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
