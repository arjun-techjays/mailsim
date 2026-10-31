# mailsim

Deterministic, provider-neutral email simulation engine for testing AI agents.
Implements **Phase 1 (core simulation engine)** of
[`email-agent-testing-simulator-spec.md`](email-agent-testing-simulator-spec.md).

> Status: Phase 1 engine + Waves 1-2 of Phases 2-5: REST API `/v1`, hidden answer keys and the
> post-run evaluator, LLM gateway (stub / Claude / Ollama / Claude Code CLI) with persona runtime
> and judge, the operator dashboard (`web/`), and the agent interfaces: Gmail API v1 and Microsoft
> Graph v1.0 compatibility profiles, IMAP4rev1 + SMTP submission listeners and an MCP server.
> CI/benchmark command: `sim bench run` / `sim bench compare` ([`docs/bench.md`](docs/bench.md)).
> Not yet built: hosted controls (Wave 3).

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
(`GET /v1/runs/{run_id}/activity`). A connection test is an operator check, not agent activity:
it only reads, arms no trigger, and is listed with the actor `connection_test` (never graded,
never counted as an agent action). Only that operator route can mark a call this way.

## CI regression suite (`sim bench`)

`sim bench run <packs...> --build <id> --agent-cmd "<cmd>"` (or `--agent-callable mod:fn`)
publishes fixed scenario packs, runs `--trials N` per scenario against the agent build,
grades each run with the pack's answer key, and writes a JSON result (`--json`) and a JUnit
report (`--junit`). Each run records the scenario version, build id, seed, models, duration,
action counts and model usage. Per scenario the result gives the pass rate and the variance.
It exits 1 when a scenario's pass rate is below `--min-pass-rate` (default 1.0) or its
accuracy variance is above `--max-variance`. `sim bench compare base.json candidate.json`
lists per-scenario and per-assertion deltas and exits 1 on a regression. Details and the
agent environment contract: [`docs/bench.md`](docs/bench.md).

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

## Answer keys, evaluations and replays

* **Answer-key references are checked** at validate (`POST .../answer-keys/validate`) and at
  publish (also for inline scenario assertions). Unknown names are errors with dotted paths
  (`answer_key.assertions.3.check.where.folder`), never a silent PASS or a forever-INCONCLUSIVE:
  folders (`inbox sent drafts trash archive`, any case), flags (`SEEN ANSWERED FLAGGED DELETED
  DRAFT`, any case), operations per `actor` (any case; agent: the scenario agent operations; simulator:
  `persona.reply persona.failed`; controller: `create_run start cancel error reset inject tick
  advance replay`), outcomes (`ok`, `any` or an error code, any case), protocol `error_codes`, and the
  persona mailboxes and event ids (`evaluate_at.event`, temporal `first`/`then`) of **every**
  scenario version the key is compatible with (`scenario_versions`, all when omitted). Labels
  are free-form and not checked. A key that covers all versions (`scenario_versions` omitted)
  is checked against the versions that exist **when it is published**: publishing a later
  scenario version does not re-check stored keys (a name missing from that version then grades
  as it always did, e.g. `no mailbox for persona` or INCONCLUSIVE). List `scenario_versions`
  explicitly, or publish a new key version, when a new scenario version renames things.
* **YAML booleans.** Only `true`/`false` are booleans; `on`/`off`/`yes`/`no` stay strings
  (YAML 1.2 rules), so a persona script's `on:` key and `checks_default: on` work unquoted.
  Where a boolean is expected (`once: yes`), those strings are still accepted.
  `checks_default` is `on` or `off`; `true`/`false` and `yes`/`no` (any case) work too.
* **System labels and flags in `where`** are graded from what happened, not from what is
  stored: `label: SENT` = sent by a real send, `DRAFT`/`DRAFTS` = a real draft, `INBOX`/`TRASH`/
  `ARCHIVE` = the folder, `UNREAD` = not seen; `ANSWERED` (label or `has_flag`) in the agent's
  mailbox = the agent really replied to it (a reply, or sent mail whose `In-Reply-To` names it and
  that went to its sender; a forward is not an answer). `STARRED`/`FLAGGED` and `DELETED` are
  flags the agent sets itself: they show what it did to a message, not that it did the work,
  so pair them with an `action` check (e.g. `operation: reply`) when that is what you grade.
  Labels match ignoring case and surrounding blanks. Two edge cases for authors: a reply to
  the agent's own sent message goes to that message's recipients, not to the agent, so it is
  not graded as "answered" for that message; and a self-addressed send (every recipient is the
  agent's own address) answers nothing, so seeded inbound mail whose `From` is the agent's own
  address cannot be graded `ANSWERED` by a send to self.
* **`operation: reply` means an answer**, by the same rule as `ANSWERED`. In `action` checks and
  temporal `first`/`then` action refs, a completed `reply` counts only when its mail reached
  the replied message's sender (`From` or `Reply-To`, any case; Cc and Bcc count). It must also
  answer a message the agent did not make itself: replies to its own sent mail, or to mail it
  sent to itself, inserted or drafted (or a copy of such mail), never count. Mail the agent
  copied or inserted that is byte-identical to an earlier message of its mailbox (IMAP COPY /
  APPEND, Gmail insert of the same bytes) counts like that message, so a copy of received mail
  counts like the original. Every interface records a client's reply as one `reply`, whoever
  it is addressed to: SMTP or Gmail mail whose In-Reply-To (else References) names a message,
  Graph `reply` with `toRecipients`, a reply draft re-addressed and sent. So a "reply" sent
  only to self or to a third party is in the action log but is not counted. The `action` diff
  lists such replies under `excluded_not_answering`, with `excluded_reasons` per action
  (`sender_not_reached`, `own_message`, `unknown_message`). Refused attempts are not
  affected: with `outcome: any` or an error code (e.g. `PERMISSION_DENIED`), a refused reply
  still counts, as recorded. A policy `forbid: {operation: reply}` still matches **every**
  reply operation, whoever it reached: use it to prohibit replying at all.
* **Budget and action accounting (for `action` counts and audit expectations).** What an
  `action` check counts is the audit log (`GET /v1/runs/{id}/actions`): one row per engine
  command, agent writes, reads and refusals alike. What the caps count is narrower:
  * *Write budget (`max_agent_actions`).* One unit per agent write command, succeeded or failed
    after the guard; reads, polling and `sent_copy` cost nothing.
  * *One write per new message (since PR #16, issue #30).* A Gmail `messages.insert` / `import`
    with `labelIds`, and an IMAP `APPEND` with flags or keywords, is a **single** `insert`
    action and one budget unit, stored with all its flags and labels or not at all. Before #16
    it recorded up to three actions (`insert`, then `flag`, then `label`). An `action` check on
    `operation: flag` or `operation: label` (or a `max:` / `eq:` count of them, or of all writes)
    no longer sees those implied changes: grade the result with a `state` check (`where.label`,
    `has_flag`) or count `operation: insert`. `after_agent_action` triggers on `flag` / `label`
    still fire for such an insert, so scenario timelines are unchanged.
  * *Logins are not actions (since PR #16, issue #30).* A **successful** IMAP `LOGIN` /
    `AUTHENTICATE` or SMTP `AUTH` no longer writes an agent `read` row; a refused one (run not
    active, another persona's mailbox, ...) still records a denied agent action, and an
    operator's connection test records its probe with the actor `connection_test` (never
    graded). A `count` of agent `read` actions written before #16 that included one read per
    protocol login needs lowering.
  * *Reads (`max_agent_reads`, issue #24).* Budgeted once per agent request: one HTTP request
    (a REST/Gmail/Graph page), MCP tool call, IMAP command (its implicit change-feed poll
    included) or SMTP transaction is one read, however many messages it touches; an item of a
    Gmail batch or thread operation pays its own. The audit still has one `read` row per engine
    read, so row counts differ by interface: an IMAP `FETCH` of N bodies records about N + 1
    `read` rows (plus one per message it marks `\Seen`), a Gmail `messages.list` page one. Do
    not grade efficiency with exact `operation: read` counts across interfaces; bound it with
    `evaluation.limits.max_agent_reads` and `invariant: no_limit_exceeded` (`limit:
    max_agent_reads`) instead.
  * Reads that build the response to a write the same request already committed (read-backs)
    are recorded but never counted or refused, so a committed write never answers "quota
    exceeded".
* **Evaluation errors.** `POST /v1/runs/{id}/evaluations` answers 404 when the requested
  `answer_key_version` does not exist and 409 CONFLICT when it (or every key) is not compatible
  with the run's scenario version (`details.answer_key_version`, `details.scenario_versions`,
  `details.run_scenario_version`).
* **Builds.** Runs created without `agent_build` store null; `/accuracy` groups them as
  `(no build)` (a reserved name: run creation refuses it, and blank names). A build literally
  named `unspecified` is an ordinary build.
* **Replays** (`POST /v1/runs/{id}/replay`) copy the recorded event log into a new run with
  `replay_of` set; the copy ends `REPLAYED` (or keeps the original's final status when the
  original had already ended) and takes no further commands. A replay has no agent actions, so
  it is never graded: evaluating it answers 409 (`details.replay_of`: evaluate the original).
  Replays are left out of `/accuracy` and of `GET /v1/runs` unless `include_replays=true`
  (or `status=REPLAYED`).
* **Downloads.** Reports (`.../evaluations/{evaluation_id}/report?format=json|junit|markdown`)
  download as `<run_id>-<evaluation_id>.<json|xml|md>`. Every blob in `GET /v1/runs/{id}/artifacts`
  carries a `url` (`GET /v1/runs/{id}/artifacts/{ref}`, controller or evaluator; reports
  evaluator only) served as an attachment with `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: sandbox`.

## Release notes

* **Self-addressed replies are no replies anywhere (issue #26).** A reply whose every recipient
  is the replier's own address no longer marks the original `ANSWERED` (IMAP `\Answered`,
  Gmail `ANSWERED` label, Graph replied marker), and the efficiency metric `replies_sent` no
  longer counts such sends. Engine and grading share one rule (`is_self_addressed` in
  `domain/models.py`). Recorded event logs are unchanged and replay as recorded: an older run
  whose log holds an `ANSWERED` flag change after a self-reply keeps that flag on replay;
  re-grading it ignores the flag (grading derives `ANSWERED` from the replies, not the flag)
  and recomputes `replies_sent`. Grading also only takes byte-identical sources for copies /
  inserts from the current epoch (mail from before a reset no longer counts as "earlier").
* **`operation: reply` counts answers only (issue #5).** In `action` and `temporal` checks a
  completed `reply` counts only when it reached the replied message's `From`/`Reply-To` (see
  the answer-key notes above); policy `forbid` is unchanged. **Re-grading:** stored evaluation
  reports are not rewritten, but evaluating an older recording again can differ. A reply sent
  only to self, only to third parties, or to mail the agent made itself no longer counts. So
  `count: {min: ...}` checks on `reply` can go PASS -> FAIL (e.g. `replied-to-vendor` in
  `finance.invoice-bank-change`), `eq`/`max` checks FAIL -> PASS, and temporal checks anchored
  on such a reply change accordingly. `ANSWERED` was tightened the same way: a reply action
  now answers with the recipients of its own `message.sent` only (another actor's mail in the
  same event window no longer counts), which no current interface produces.
* **QA-D (agent writes, connection tests).** System labels are derived, never written by the
  agent: `SENT DRAFT DRAFTS INBOX TRASH ARCHIVE UNREAD STARRED ANSWERED DELETED` (any case)
  can't be added or removed as labels through any interface (REST/MCP `INVALID_REQUEST`, Gmail
  user-label ids spelling them 404, Graph categories 400, IMAP keywords `NO [CANNOT]`); the
  `DRAFT` flag can't be put on mail that is not a draft nor taken off a draft
  (`PERMISSION_DENIED`); agents can't insert/APPEND into Sent or Drafts, and only mail the
  agent really sent may be moved or copied (back) into Sent. In answer keys, `where.label` on a
  system label and `has_flag`/`lacks_flag: DRAFT` are graded from provenance (`SENT` = created
  by a real send, `DRAFT` = a real draft, `INBOX`/`TRASH`/`ARCHIVE` = folder, `UNREAD`/
  `STARRED`/`ANSWERED`/`DELETED` = flags), so stored labels can't fool them (older recordings
  re-grade accordingly). Dashboard connection tests are recorded with the actor
  `connection_test` (see above). Answer-key operation and outcome names match ignoring case;
  `GET /v1/health` also reports `local_no_auth`.
  Follow-ups: a refused system-label / DRAFT / Sent-Drafts write is recorded as a refused agent
  `label` / `flag` / `insert` action on every interface (also when an adapter refuses it before
  its first write). IMAP APPEND to `Sent` answers OK without storing anything (recorded as
  `sent_copy`: no permission, no budget, not counted, never evidence); APPEND to `Drafts`
  from this mailbox's address makes a real draft. `where.label: answered` and
  `has_flag: ANSWERED` count only messages the agent really replied to (forwards don't count).
  **Breaking:** moving controller-seeded Sent history out of `Sent` and back is refused (only
  mail sent during the run counts as sent). `/openapi.json` and `/docs` are served to operators
  only (controller / evaluator keys, the local no-auth operator): no credential 401, agent key
  404. A browser can't send a key when you open `/docs`, so read them with curl or an API
  client (`curl -H "Authorization: Bearer $CONTROLLER_KEY" http://127.0.0.1:8000/openapi.json`);
  the dashboard and `openapi/openapi.json` are unaffected.
  Thunderbird over IMAP: a sent copy filed into `Sent` is accepted and not stored twice; a draft
  saved into `Drafts` becomes a real draft, but drafts with attachments can't be saved over IMAP
  (`NO [CANNOT]`) and the draft is rebuilt from its parts, so client headers such as
  `X-Mozilla-*` are not kept.

* **QA-A3 (evaluation, validation, runs).** Answer keys with unknown references are refused
  (validate and publish). New run status `REPLAYED`; replay runs are not evaluated and are
  hidden from `/accuracy` and the runs list by default (`include_replays=true`). Incompatible
  answer keys answer 409. Report and artifact downloads are attachments
  (`GET /v1/runs/{id}/artifacts/{ref}`); only passive content types are echoed, everything
  else (HTML, SVG, XML, scripts) is `application/octet-stream`. **Breaking:** `/accuracy`
  reports runs without a build as `(no build)` (was `unspecified`): `builds=unspecified` no
  longer selects them, use `builds=(no build)`.

* **QA D-1 (scenario packs, duplicate mail).** Validate and publish refuse a mailbox that
  starts with the same message twice: a fixture listed twice in one mailbox, or two fixtures
  with the same `Message-ID` (the error names both fixtures, at
  `scenario.mailboxes[i].initial_messages[j].fixture`; API issue paths spell it dotted,
  `scenario.mailboxes.0.initial_messages.2.fixture`). The same message in several mailboxes
  is fine; scheduled events and persona replies get unique generated ids. **Breaking:** a scenario
  that relied on duplicate initial messages no longer publishes (every bundled scenario passes).
  In the builder, uploaded `.eml` files fill the fixtures a pasted YAML lists, matched by exact
  file name (a trailing `-` is kept), and never add a second copy; files that match nothing are
  held until you choose to add them. New dashboard page **Scenarios → Import scenario pack**.

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

## Leadership inboxes (pseudonymized Enron corpus)

`scenarios/leadership/` holds CEO, CFO, CBO and Director of Finance inboxes of a fictional
company, generated from the public Enron corpus by `sim import enron` and
`sim generate leadership`. They are pseudonymized, topic-routed and threaded, with injected
risk items and minimal answer keys. Greetings and sign-offs match the bound personas, records
that do not fit their sender are skipped, dates follow the scenario clock, and messages that
say something is attached carry a generated PDF or XLSX branded by the sender's organisation.
Identifiers are hashed with a committed, deliberately public key
(`src/mailsim/importers/data/enron-id.key`) that keeps regeneration reproducible. `sim import scan` checks headers, decoded bodies and attachment text, including
real organisations and any 6+ digit run that is not a listed hashed id. See
[`docs/leadership-inboxes.md`](docs/leadership-inboxes.md).

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
  artifacts are reported, not regenerated. The copy is then closed (`REPLAYED`, see above).
* **No external delivery.** `DeliveryPolicy` is the only place recipients are resolved: simulated
  mailboxes, or the external mode `deny` (default, whole send rejected) / `sink` / `event`.
  There is no SMTP/socket/HTTP client in `src/` (ruff banned-api + AST test), tests patch sockets
  to prove none is opened, and the Compose `app` service sits on an `internal` network.
  **Gap:** the host-level egress block for real deployments is only verifiable once Phase 4
  adds listeners; Phase 1 proves the in-process guarantee and ships the deny-by-default network.
* **Tenancy.** Application-scoped (`Scope(tenant, project)` on every repository/engine call;
  out-of-scope resources are `NOT_FOUND`). Row-level security is deferred to Phase 2 with auth.

## Release notes: engine semantics (QA-A1)

Re-grading a run recorded before this change can give a different result:

* **Blocked external attempts count.** A send refused for a recipient outside the simulation
  (deny mode, prohibited external delivery, SMTP `RCPT TO` answered 550) is an attempt:
  `no_external_delivery_attempts` / `no_external_recipients` fail on it (older recordings are
  graded from the EXTERNAL_DELIVERY_BLOCKED action). Both invariants count only the AGENT's
  attempts; a persona's or the controller's do not. An envelope refusal is a budgeted `send`
  action (`delivery.external_blocked` event); an SMTP session is closed (421) after 20 of them.
  The bundled finance answer key is now **version 3** (`no-real-delivery` reworded).
* **`folder: sent` is provenance, not placement.** Only messages created by a real send count as
  sent (and only real drafts as drafts); agents can no longer move or copy received mail into
  Sent or Drafts (PERMISSION_DENIED).
* **Checks off with nothing decided is INCONCLUSIVE.** When a key has mandatory grading
  assertions and none of them was decided (all NOT_APPLICABLE), invariants alone no longer make
  the run PASSED and accuracy is blank. Invariant-only keys are graded as before.
* A reply or forward drafted first (Graph createReply/createForward, Gmail drafts with
  In-Reply-To) is sent as ONE `reply` / `forward` (ANSWERED, `send_reply` triggers, policies).
* Persona replies are stamped at their due virtual time; one advance may record several
  `advance` actions (one per stop).

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
* Per-run resource caps (spec 10), optional in the scenario's `evaluation.limits`, bound what
  the budget does not: `max_agent_reads` (agent `read`/`search` commands, failed ones included;
  default 10000), `max_sent_copies` (IMAP copies filed into Sent; default 1000),
  `max_agent_events` (events attributed to the agent since the last reset; default 20000) and
  `max_labels_per_message` (labels/keywords/categories the agent leaves on one message; default
  50; removing is always allowed). Unset caps use the defaults and are not written into the
  scenario's canonical form, so existing versions keep their spec hash. A reached cap is the
  same quota error as the budget on every interface (agents see `QUOTA_EXCEEDED` / "action
  limit reached"), recorded as a `SIMULATION_LIMIT_EXCEEDED` action whose `details.cap` names
  the cap; `invariant: no_limit_exceeded` fails on any of them and takes an optional
  `limit: <cap name>` to check one. Reads are budgeted per agent request, not per engine read
  (issue #24): one HTTP request (REST/Gmail/Graph page), MCP tool call, IMAP command or SMTP
  transaction costs at most one `max_agent_reads` unit however many engine reads it makes (an
  IMAP `FETCH 1:*` of every body, with its change-feed poll, is one read). Reads an
  interface makes to answer a write that request already committed (Gmail returning the sent
  message, a REST/Graph PATCH returning the message, an IMAP re-sync after STORE) are recorded
  but neither counted nor refused, so a committed write never answers "quota exceeded"; reads
  before the write, and requests that write nothing, count as usual. The event cap is checked
  before a command, so the command that reaches it completes and may exceed it by its own
  events (a send: its copies and deliveries; an expunge or bulk delete: one per message). A
  label-cap refusal is a failed write and costs budget; a new message (Gmail insert/import,
  IMAP APPEND) with more labels than the cap is refused before it is stored. Each item of a
  Gmail batch or thread operation pays its own read (one item's read or write exempts nothing
  for the next, so a repeated id buys nothing). The mailbox check at IMAP LOGIN / SMTP AUTH is not an agent read: an agent at its
  read cap can still connect and send. Refusals still write an audit row each.
* **Gmail insert/import and IMAP APPEND are one write.** A message's initial flags and labels
  (`labelIds`, APPEND flags/keywords) are applied in the same `insert`: stored with all of them
  or not at all, and one budget unit where it used to be up to three (`insert`, `flag`,
  `label`). `after_agent_action` triggers on `flag` / `label` still fire for such an insert.
* The audit action log records recipients, counts and sizes but not subjects, bcc or bodies.
* `actor` is a trusted-caller argument; Phase 2/3 must expose agent-facing facades that hard-code it.

## Troubleshooting

* **PostgreSQL takes minutes to come back after a crash** ("syncing data directory (fsync)" in
  its log): crash recovery syncs every file of every database first. The Compose service runs
  with `recovery_init_sync_method=syncfs` (one sync per file system); recreate the container to
  apply it (`docker compose up -d postgres`). Keep the number of databases down: the test suite
  drops its per-run helper databases (`<MAILSIM_TEST_DB>_local`, `_migration`) when done; old
  ones from earlier runs can be dropped by hand (`DROP DATABASE ... WITH (FORCE)`).

See the PR notes: provider id mapping tables, PASSED/FAILED verdicts (need the Phase 2
evaluator), scheduled events support only `email.receive`, persona runtime is not implemented.
