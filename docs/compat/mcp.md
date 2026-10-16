# MCP server compatibility (spec §8)

mailsim exposes the simulator to MCP clients (Claude Code, Claude Desktop, any client built on
an official MCP SDK) as one server, `mailsim`, over **stdio** and **streamable HTTP**. MCP is
an interface to the simulator, not the engine: every tool runs through the same role facades
as the REST API (`engine/facades.py`), so the same guards apply (agent permissions, run
state, write budget, delivery policy) and every call is audited in the run's event log.

Code: `src/mailsim/mcp/` (`tools.py` catalogue, `server.py` factory, `http.py`, `stdio.py`).
Built on the official Python SDK (`mcp` 2.3, low-level `mcp.server.Server`).

## Authentication and roles

Credentials are the sandbox API keys used by REST (`msk_<prefix>_<secret>`), one role each:

| Key | Issued by | Bound to | Sees |
| --- | --- | --- | --- |
| agent | `POST /v1/scenarios/{key}/runs` or `POST /v1/runs/{id}/agent-credentials` | one run + its agent mailbox | mailbox tools |
| controller | `python -m mailsim.serve bootstrap`, `POST /v1/projects` | tenant + project | simulation-control + inspection tools |
| evaluator | same as controller | tenant + project | inspection + evaluation tools |

- **stdio**: the key is read from `MAILSIM_API_KEY` at startup. The server refuses to start
  without a valid key.
- **streamable HTTP**: `Authorization: Bearer <key>` on every request. A missing or invalid
  key is answered `401` (REST envelope, code `UNAUTHENTICATED`, `WWW-Authenticate: Bearer`)
  before any MCP processing.
- The key is **re-verified on every `tools/list` and `tools/call`** in both transports.
  Revoking it, expiring it, or ending the agent's run (cancelled / passed / failed / error)
  ends access at once, even inside an open stdio session.
- There is **no unauthenticated mode**. `MAILSIM_LOCAL_NO_AUTH` (dashboard / control plane)
  never grants MCP access. Agent tools always need an agent key.

**Authorization is enforced per call, not by hiding tools.** `tools/list` returns only the
caller's role's tools, but this is for convenience. Each `tools/call` builds the caller's
facade with `facade_for(principal)` and refuses a tool whose facade differs, with
`PERMISSION_DENIED`, whether or not the tool was listed (tested for every role pair). An agent
key's facade is bound to its run + mailbox. A tool taking a `mailbox_id` or `run_id` cannot
reach another mailbox, run, project or tenant. Answer keys and evaluation data are reachable
only through `get_test_results` (evaluator role). No agent tool returns scenario, answer-key
or evaluation content.

## Tools

`*` = required. Every input schema is an explicit JSON Schema object with
`additionalProperties: false`. Unknown arguments are rejected with `INVALID_REQUEST`. Every
tool is annotated `openWorldHint: false`, because nothing leaves the sandbox. Results come
back as `structuredContent` (a JSON object), with the same JSON as text content.

### Agent (spec 8.1): bound to the key's mailbox

| Tool | Arguments | Result |
| --- | --- | --- |
| `list_messages` | `folder` (id, name or system type: inbox, sent, drafts, trash, archive), `page_size` 1-500 = 50, `cursor`, `order` asc\|desc = asc | `{items: [message], next_cursor}` |
| `get_message` | `message_id`* | message |
| `get_thread` | `thread_id`* | `{items: [message]}` |
| `search_messages` | `query`* (subject, body, sender), `folder`, `page_size`, `cursor` | `{items, next_cursor}` |
| `get_attachment` | `message_id`*, `attachment_id`* (an `attachments[].id` of the message) | `{id, filename, mime_type, size, checksum, content_base64}`; over 10 MB: `UNSUPPORTED_OPERATION` with `details.size` / `details.max_bytes` |
| `send_message` | `to`, `cc`, `bcc` (address lists; `Name <addr@x>` accepted), `subject`, `text`, `html` | `{message_id, message_id_header}` |
| `reply_to_message` | `message_id`*, `text`*, `reply_all` = false | same as send |
| `forward_message` | `message_id`*, `to`* (1+ addresses), `cc`, `text` (above the forwarded message) | same as send; the original's attachments are forwarded |
| `manage_labels` | `message_id`*, `folder` (move), `add_labels`, `remove_labels`, `add_flags`, `remove_flags` | the updated message |
| `delete_message` | `message_id`* | `{deleted}` |
| `list_folders` | none | `{items: [{id, name, system_type}]}` |
| `get_changes` | `since` = 0, `limit` 1-500 = 500 | `{changes, next_since, has_more, reset_since}` |

A *message* is the canonical message of the REST API (`GET /v1/mailboxes/{id}/messages/{mid}`):
`id, thread_id, folder, from, to, cc, subject, body_text, body_html, in_reply_to, references,
labels, flags, attachments, created_at, ...`. Pagination is keyset-based. Under a fixed state,
following `next_cursor` until it is `null` never skips or repeats an item.

`send_message`, `reply_to_message` and `forward_message` deliver only to simulated mailboxes.
For recipients outside the simulation, the scenario's `delivery.external_delivery` rule
applies. `deny` gives a tool error `EXTERNAL_DELIVERY_BLOCKED`. `sink` and `event` record the
message in the sandbox. Real SMTP is never used. The agent's result is only the sent copy
(`message_id`, `message_id_header`) and is the same in every mode: how each recipient was
handled is visible to operators only (the run's actions and `delivery.*` events). Address
entries may be `Name <addr@example.com>` (also several, comma-separated); the name is dropped.

`manage_labels` applies its changes as separate audited writes, in this order: move, labels,
flags. It resolves the folder (`NOT_FOUND`) before the first write. If a later step fails, the
earlier ones remain, the same as `PATCH /v1/mailboxes/{id}/messages/{mid}`.

### Simulation control (spec 8.2): controller role

| Tool | Arguments | Result |
| --- | --- | --- |
| `inject_incoming_email` | `run_id`*, `mailbox_id`*, then either `from` + `to` (+ `cc`, `subject`, `text`, `html`, `in_reply_to`, `references`) or `raw_base64` (RFC 5322) alone (combining it with any message field is `INVALID_REQUEST`); `event_id` | `{event_id, message_id, delivery_status: delivered_to_simulated_mailbox}` |
| `advance_simulation` | `run_id`*, `seconds`* ≥ 0 | `{fired, virtual_time, persona_replies}` |
| `trigger_scheduled_event` | `run_id`* | `{fired, virtual_time, persona_replies}`: fires what is already due, and the clock does not move |
| `reset_run` | `run_id`* | run status (READY, next epoch) |
| `start_run` | `run_id`* | run status |
| `cancel_run` | `run_id`* | run status. The run's agent keys are revoked. |
| `replay_run` | `run_id`* | `{replay_run_id, matches, original_hash, replay_hash, event_count, missing_blobs}` |

`advance_simulation` and `trigger_scheduled_event` then run one persona-runtime step, when the
server was given a `persona_runtime_factory` (as `serve.build_state` does). This matches the
REST `/clock/advance` and `/scheduler/tick`.

### Inspection and evaluation (spec 8.3)

| Tool | Roles | Arguments | Result |
| --- | --- | --- | --- |
| `get_run_status` | controller, evaluator | `run_id`* | `{id, status, epoch, virtual_time, agent_actions, replay_of}` |
| `list_run_mailboxes` | controller, evaluator | `run_id`* | `{items: [mailbox]}` |
| `get_run_trace` | controller, evaluator | `run_id`*, `cursor`, `page_size` 1-500 = 100, `type`, `actor`, `epoch` | `{items: [event], next_cursor}` |
| `get_test_results` | evaluator | `run_id`*, `evaluate` = false; when evaluating: `answer_key_version`, `checks` on\|off, `complete_run` = true | the evaluation report (JSON, same as `GET /v1/runs/{id}/results`) |

With `evaluate: false`, `get_test_results` returns the latest stored evaluation (`NOT_FOUND`
if none exists). With `evaluate: true`, it grades the run now through `EvaluationService`.
When that completes the run, its agent keys are revoked, as with REST.

## Errors

Tool failures come back as tool results with `isError: true`, so models can read them and
correct course. Protocol errors are not used for them:

```json
{"isError": true,
 "content": [{"type": "text", "text": "EXTERNAL_DELIVERY_BLOCKED: ..."}],
 "structuredContent": {"error": {"code": "EXTERNAL_DELIVERY_BLOCKED", "message": "...", "details": {}}}}
```

`code` is one of the spec 7.4 codes (`INVALID_REQUEST`, `NOT_FOUND`, `PERMISSION_DENIED`,
`UNSUPPORTED_OPERATION`, `CONFLICT`, `RATE_LIMITED`, `SIMULATION_LIMIT_EXCEEDED`,
`RUN_NOT_ACTIVE`, `EXTERNAL_DELIVERY_BLOCKED`, `UPSTREAM_UNAVAILABLE`), plus:

- `UNAUTHENTICATED`: the key stopped being valid mid-session.
- `INTERNAL`: an unexpected fault. Details are never included.

`details` are preserved; for example, `UPSTREAM_UNAVAILABLE` keeps its `retryable` flag.
Invalid arguments give `INVALID_REQUEST` with `details.errors[] = {loc, msg}`. An unknown tool
name gives `UNSUPPORTED_OPERATION`. On stdio, a `tools/list` with a revoked key fails with a
JSON-RPC error (`-32600`, "missing or invalid API key").

## Transports

### Streamable HTTP: `POST /mcp`

The REST app mounts the endpoint (`serve.mount_adapters` includes `mailsim.mcp.http.router`), so
it runs on the same host and port as `/v1` (`python -m mailsim.serve serve`, default
`http://127.0.0.1:8000/mcp`).

- **Stateless**: no `Mcp-Session-Id`. Each POST is self-contained and authenticated, and
  responses are JSON (`application/json`), not SSE.
- **Methods**: only POST is routed. `GET` (server notification stream) and `DELETE` (session
  end) answer `405`, which the MCP spec allows. Without sessions there is nothing to stream
  or end, and the server sends no notifications.
- **Protocol versions**: both the handshake era (`initialize`, e.g. `2025-11-25`) and the
  2026-07-28 per-request era (`server/discover`) are served. Both are tested with the official
  client (`mode="legacy"` and `mode="auto"`).

### stdio

The `mcp-stdio` subcommand of `python -m mailsim.serve` calls `mailsim.mcp.stdio.run_stdio`. The
key comes from `MAILSIM_API_KEY` only (never argv); stdout is the protocol channel and logs go to
stderr; local no-auth mode never applies; a missing or invalid key exits with status 2.

```
MAILSIM_API_KEY=msk_... python -m mailsim.serve mcp-stdio
```

The process uses `MAILSIM_DATABASE_URL` and `MAILSIM_BLOB_DIR` like `serve`. It serves one
key for its lifetime. Run one process per role or agent.

## Connecting a client locally

1. Start Postgres and bootstrap a project (`python -m mailsim.serve bootstrap --migrate
   --output keys.json`) to get controller and evaluator keys.
2. Publish a scenario and create a run with the controller key
   (`POST /v1/scenarios/{key}/runs`). The response carries the run's agent key, once.
3. Give the agent the agent key only. Keep the controller and evaluator keys in the harness.

### Claude Code

HTTP (server running with `python -m mailsim.serve serve`):

```bash
claude mcp add --transport http mailsim http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer msk_xxxxxxxxxxxx_agent-key"
```

stdio:

```bash
claude mcp add mailsim --env MAILSIM_API_KEY=msk_xxxxxxxxxxxx_agent-key \
  --env MAILSIM_DATABASE_URL=postgresql+psycopg://mailsim:mailsim@127.0.0.1:54329/mailsim \
  -- python -m mailsim.serve mcp-stdio
```

Or in a project `.mcp.json`:

```json
{
  "mcpServers": {
    "mailsim": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {"Authorization": "Bearer ${MAILSIM_API_KEY}"}
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "mailsim": {
      "command": "/path/to/repo/.venv/bin/python",
      "args": ["-m", "mailsim.serve", "mcp-stdio"],
      "env": {
        "MAILSIM_API_KEY": "msk_xxxxxxxxxxxx_agent-key",
        "MAILSIM_DATABASE_URL": "postgresql+psycopg://mailsim:mailsim@127.0.0.1:54329/mailsim",
        "MAILSIM_BLOB_DIR": "/path/to/repo/var/blobs"
      }
    }
  }
}
```

### Other clients (official SDKs)

```python
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"})
async with http, Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)) as c:
    page = await c.call_tool("list_messages", {"folder": "inbox"})

params = StdioServerParameters(
    command="python",
    args=["-m", "mailsim.serve", "mcp-stdio"],
    env={"MAILSIM_API_KEY": key, "MAILSIM_DATABASE_URL": url},
)
async with Client(params) as c:
    tools = await c.list_tools()
```

## Deviations and notes

- **Stateless HTTP only.** There are no MCP sessions, no SSE notification stream, and no
  resumability. Credentials are per request, and every tool is request/response.
- **`replay_run` is controller-only.** Spec 8.3 lists it with the evaluation tools, but the
  frozen `EvaluatorView` facade has no replay and REST `POST /v1/runs/{id}/replay` is also
  controller-only. Adding `EvaluatorView.replay_run` (integrator) would let evaluators replay.
- **Inspection tools are shared.** `get_run_status`, `get_run_trace` and
  `list_run_mailboxes` are open to controller and evaluator keys, matching the REST
  `Inspector` routes. `get_test_results` (answer-key content) is evaluator-only.
- **Extra agent tools.** Beyond spec 8.1, `delete_message`, `list_folders` and `get_changes`
  give MCP agents parity with the REST mailbox API and the shared contract suite.
- **No run creation over MCP.** Creating runs and minting agent keys stays on REST, because
  those are control-plane records and keys are shown once.
- **No idempotency keys on MCP writes.** REST accepts `Idempotency-Key`, but MCP tool calls
  have no equivalent. A retried call runs again: a retried `advance_simulation` advances the
  clock twice, and a retried `send_message` sends twice. Read the state back with
  `get_run_status` / `get_run_trace` before you retry a write.
- **No DNS-rebinding guard is needed on `/mcp`.** The SDK's Host/Origin guard is not
  enabled, because every request must carry a sandbox Bearer key and a browser page cannot
  supply one. The route otherwise inherits the REST app's settings. Bind to `127.0.0.1`
  (the default) for local use.
- **Argument bounds.** Ids, folder names, labels and flags are at most 256 characters. Lists
  (`to`, `cc`, `bcc`, labels, flags, references) are at most 500 items. `query` is at most
  1024 characters, subjects at most 16 KiB, and `text`/`html` at most 5 MiB. `raw_base64`
  has the REST size cap. Larger input is rejected with `INVALID_REQUEST`, and the engine's
  MIME limits still apply.
