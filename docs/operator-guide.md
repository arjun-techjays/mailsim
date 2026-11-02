# Operator guide

Running the server, dashboard and CLI, publishing scenario packs, and the local-only
conveniences. New here? Start with the [tutorial](tutorial.md).

## Agent keys, endpoints and errors

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
(`GET /v1/runs/{run_id}/activity`). A connection test is an operator check, not agent activity:
it only reads, arms no trigger, and is listed with the actor `connection_test` (never graded,
never counted as an agent action). Only that operator route can mark a call this way.

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

## The engine from the command line (no server)

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

## Scenario packs: import from the dashboard or the CLI

A pack is a folder (or a `.zip` of one) with `scenario.yaml` (or `.json`), the `.eml` files it
lists (paths relative to it) and, optionally, `answer_key.yaml`; the leadership inboxes under
`scenarios/leadership/` are packs. Other files are not read.

* **Dashboard:** Scenarios → *Import scenario pack* → choose the folder or the `.zip`. The pack
  is checked (scenario problems, answer-key problems, unused files), then one click publishes
  the scenario and its answer key. Needs a controller key or the local no-auth operator. The
  `.zip` reader refuses encrypted, ZIP64, symlinked, oddly compressed or unsafe (`..`, absolute)
  entries and stops inflating at the declared size; limits: 30 MB `.zip`, 400 entries,
  200 `.eml` files of at most 5 MB, 25 MB unpacked.
* **CLI:** `sim publish <dir|scenario.yaml> [--answer-key answer_key.yaml]` and
  `sim answer-key publish <answer_key.yaml>` publish into an EXISTING project: `--project <id>`,
  else `MAILSIM_LOCAL_PROJECT_ID`, else the only project in the database (the one the local
  no-auth dashboard uses); they never create a project.
  * **Without `--api-key-env` this is privileged operator access:** the CLI writes straight to
    the database in `MAILSIM_DATABASE_URL` with no key and no role checks (anyone who can run it
    with that URL is the operator). The scenario and its answer key are stored in one
    transaction.
  * **With `--api-key-env VAR`** the CLI goes through the API's routes with the key in that
    environment variable (roles: scenarios need a controller key, answer keys a controller or
    evaluator key; the project is the key's). It publishes the scenario, then the key; if the key
    is refused, the error output names the scenario version that was published (`published`).
    Keys are read from the environment only and never printed.
  * **Same database and blob store as the server:** set `MAILSIM_DATABASE_URL` and
    `MAILSIM_BLOB_DIR` to the server's values (fixtures are stored in the blob directory; with a
    different one, runs of the published version miss their messages).
  * **No `--api-url` (trade-off):** nothing in `src/` may open a network connection (ruff
    banned-api and the safety test keep external delivery impossible), so the CLI cannot call a
    server over HTTP. It runs the same API routes in-process against the server's database
    instead, which means it must run on a machine that can reach that database and blob
    directory.

```bash
export MAILSIM_DATABASE_URL=postgresql+psycopg://mailsim:mailsim@localhost:54329/mailsim
export MAILSIM_BLOB_DIR=var/blobs        # the server's blob directory
uv run sim publish scenarios/leadership/cbo --answer-key scenarios/leadership/cbo/answer_key.yaml
MAILSIM_CONTROLLER_KEY=... uv run sim publish scenarios/leadership/cbo --api-key-env MAILSIM_CONTROLLER_KEY
```

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

## Troubleshooting

* **PostgreSQL takes minutes to come back after a crash** ("syncing data directory (fsync)" in
  its log): crash recovery syncs every file of every database first. The Compose service runs
  with `recovery_init_sync_method=syncfs` (one sync per file system); recreate the container to
  apply it (`docker compose up -d postgres`). Keep the number of databases down: the test suite
  drops its per-run helper databases (`<MAILSIM_TEST_DB>_local`, `_migration`) when done; old
  ones from earlier runs can be dropped by hand (`DROP DATABASE ... WITH (FORCE)`).
