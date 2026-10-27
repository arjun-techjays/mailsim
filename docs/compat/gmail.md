# Gmail API v1 compatibility profile

mailsim serves a **defined subset** of the Gmail API v1 (spec 5.1) over the same canonical
state as the neutral REST API, IMAP/SMTP and MCP. It is a sandbox: nothing is ever delivered
outside the simulator, and the credentials are sandbox agent keys, not Google OAuth.

Code: `src/mailsim/adapters/gmail/` (router `mailsim.adapters.gmail.api:router`, mounted
automatically by `python -m mailsim.serve serve`). Tests: `tests/adapters/gmail/` and the
cross-adapter contract suite (`GmailDriver` in `tests/contract/drivers.py`).

## Connecting a client

1. Create a run as the controller (`POST /v1/scenarios/{key}/runs`); the response contains an
   **agent key** (`msk_...`) bound to that run and the agent's mailbox.
2. Point the client at the sandbox base URL and send the key as an OAuth bearer token.

```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

gmail = build(
    "gmail",
    "v1",
    credentials=Credentials(token=AGENT_KEY),
    client_options={"api_endpoint": "http://127.0.0.1:8000/"},
    static_discovery=True,
)
gmail.users().messages().list(userId="me", q="is:unread").execute()
```

Raw HTTP works the same way: `GET http://127.0.0.1:8000/gmail/v1/users/me/messages` with
`Authorization: Bearer msk_...`.

Notes for google-api-python-client:

- **Media uploads** (`media_body=...`) are sent to `https://<api_endpoint host>/upload/...`:
  the client keeps the `https` scheme of the discovery document's rootUrl and only swaps the
  host. Either put the sandbox behind TLS, or use a transport that rewrites the scheme for the
  sandbox host (see `LoopbackHttp` in `tests/adapters/gmail/test_official_client.py`). JSON
  `raw` bodies (`body={"raw": ...}`) need nothing special.
- With `google.oauth2` credentials the client answers a 401 by trying to refresh the token,
  which fails with `RefreshError` (sandbox keys cannot be refreshed). An expired or revoked key
  (the run ended) therefore surfaces as `RefreshError`.
- Batch requests (`/batch/gmail/v1`) are not supported (404).

## Authentication and isolation

- Every request needs `Authorization: Bearer <agent key>`. Missing/malformed/unknown/expired/
  revoked keys answer 401 (`UNAUTHENTICATED`), with no detail about which.
- Only **agent** keys are accepted; controller/evaluator keys answer 403. Any local no-auth or
  operator mode of the control plane (e.g. `MAILSIM_LOCAL_NO_AUTH`) is **never** honoured here.
- The key's run + mailbox decide everything: `userId` must be `me` or the mailbox address
  (case-insensitive); anything else is 403 "Delegation denied".
- The adapter reaches the engine only through the agent's `AgentMailbox` facade: scenario
  permissions (`allowed_operations` / `prohibited_operations`), the run state and the write
  budget all apply. Reads are free but audited; every write is one budgeted, audited action.
  The mailbox address and the run's virtual time come from `AgentMailbox.mailbox_info()`.
- `messages.attachments.get` reads the bytes with the audited `AgentMailbox.get_attachment`
  (one `read` with the attachment id, mailbox-scoped, 25 MiB cap). A message resource
  (`sizeEstimate`, `format=raw`, the `full` payload) is built from the stored MIME of a message
  the request already read through the facade (`get_message`, audited); that MIME is not
  recorded as a second read, unlike the REST/MCP/Graph attachment downloads.
- Every path under `/gmail/v1` and `/upload/gmail/v1`, including unknown and unsupported ones,
  checks the credential first (401 before 404/501).
- Hidden scenario data (answer keys, evaluation, scenario controls) is not reachable from any
  Gmail route.

## Operations

All paths are under `/gmail/v1/users/{userId}`; media uploads under
`/upload/gmail/v1/users/{userId}`.

| Resource | Supported | Engine operation(s) |
|---|---|---|
| `getProfile` | yes | reads |
| `messages.list` | `q`, `labelIds` (repeated, AND), `maxResults` (default 100, max 500), `pageToken`, `includeSpamTrash` | read; free text also `search` |
| `messages.get` | `format=minimal\|metadata\|full\|raw`, `metadataHeaders` | read |
| `messages.send` | JSON `raw` (+`threadId`); upload `media`/`multipart` | `send`, or one `reply` (see below) |
| `messages.insert`, `messages.import` | JSON `raw` + `labelIds`; upload `media`/`multipart` | `insert` (flags and labels in the same write) |
| `messages.modify`, `batchModify` | `addLabelIds`, `removeLabelIds` | `move` / `flag` / `label` |
| `messages.trash`, `untrash` | yes | `move` |
| `messages.delete`, `batchDelete` | yes (permanent) | `delete` |
| `messages.attachments.get` | yes | read |
| `threads.list`, `threads.get` | as messages | read |
| `threads.modify`, `trash`, `untrash`, `delete` | applied to every message of the thread | per message |
| `labels.list`, `get` (with counts), `create`, `update`, `patch`, `delete` | yes | `label` per affected message |
| `drafts.list`, `get`, `create`, `update`, `delete`, `send` | JSON and uploads | `draft`, `delete`, `send` |
| `history.list` | `startHistoryId`, `maxResults`, `pageToken`, `labelId`, `historyTypes` | read |
| `settings.*`, `watch`, `stop` | **no**: 501 `UNSUPPORTED_OPERATION` | - |
| resumable uploads | **no**: 501 | - |

`fields` (partial response, Google syntax: `a,b/c,d(e,f)`, `*`) works on every response.
`alt`, `prettyPrint`, `quotaUser`, `key` are accepted and ignored.

### Sending and replying

- The `From` header of a composed message is ignored: mail is always from the mailbox address
  (no send-as aliases). `Bcc` is honoured and never written to the wire copy.
- A message whose `In-Reply-To` (or last `References` id) names a message in the mailbox is
  executed as **one engine `reply`** with the composed message's `To`/`Cc`/`Bcc`, `Subject`,
  text and HTML bodies as overrides (addresses case-folded). It is a single budgeted `reply`
  operation: the scenario's `reply` permission applies (a scenario that allows `reply` but not
  `send` accepts it), the original is marked `\Answered`, and `after_agent_action:
  send_reply` triggers are armed. Threading headers always come from the original. If a
  recipient is outside the sandbox and the policy denies it, the whole reply fails with
  `EXTERNAL_DELIVERY_BLOCKED` and leaves nothing behind (no draft).
- Everything else is a plain `send` (one write). A reply header that matches no message in the
  mailbox is dropped (the message is sent as new).
- `threadId` must name an existing thread of the mailbox (404 otherwise); membership is decided
  by the reply headers, as in Gmail.
- Attachments in composed messages (send, drafts) are **not supported** (501): the agent facade
  has no attachment input yet. Recipients outside the simulated organisation follow the
  scenario's external-delivery rule; with `deny` the call fails with
  `EXTERNAL_DELIVERY_BLOCKED` (400 `failedPrecondition`) and nothing leaves the sandbox.

### insert / import

Places a message without sending it (never delivered to anyone). `labelIds` decide the result:
folder by precedence `TRASH` > `INBOX`, otherwise archived; without `UNREAD` the message
is read; `STARRED`/`ANSWERED`/`DELETED` set flags; user labels are applied. All of it is ONE
`insert` write: the message is stored with all of them or not at all (a refused label, the
per-message label cap, the budget). `DRAFT` is refused (400, use drafts) and, unlike Gmail, so is
`SENT` (403 `PERMISSION_DENIED`): Sent holds only mail this account actually sent, so an inserted
message can't stand in for a sent one. `internalDateSource` is
ignored (the `Date` header is used, else the virtual clock). The raw message needs a `From`.

## Identifiers

| Gmail id | Source | Lifetime |
|---|---|---|
| message `id` | `provider_ids.gmail_message_id` (16 hex, derived from the internal id) | the message's life; a draft update creates a new message id |
| `threadId` | `provider_ids.gmail_thread_id` | the thread's life; when two threads merge, the merged-away id stops resolving |
| draft `id` | `provider_ids.gmail_draft_id` | stable across `drafts.update` until sent/deleted |
| user label `id` | `Label_<hex of the UTF-8 name>` | deterministic per name; **changes on rename** |
| `attachmentId` | derived from the internal attachment id | the message's life |
| `historyId` | the run's event sequence number | the run epoch (a reset invalidates it) |

Inbound ids are translated with `AgentMailbox.resolve_external_id`, restricted to the key's
mailbox: an id from another mailbox, an internal id or a malformed id is 404. Internal ids are
never exposed. `nextPageToken`s are opaque keyset cursors (date + Gmail id): stable under a
fixed state, no duplicates or gaps.

## Labels

| Label id | Canonical state |
|---|---|
| `INBOX`, `SENT`, `DRAFT`, `TRASH` | the message's folder (`inbox`, `sent`, `drafts`, `trash`) |
| (none) | folder `archive` (Gmail "archived") |
| `UNREAD` | flag `SEEN` absent (never on drafts) |
| `STARRED` | flag `FLAGGED` |
| `ANSWERED`, `DELETED` | **extensions** for the IMAP flags `\Answered`, `\Deleted` (Gmail has no equivalent; type `system`, hidden; the names are reserved, so user labels cannot be called `Answered`/`Deleted`) |
| `Label_...` | a message label by name |
| `SPAM`, `IMPORTANT`, `CHAT`, `CATEGORY_*` | not modelled: modify -> 501; list filter matches nothing; `labels.get` 404 |

`modify` semantics: adding `INBOX`/`SENT`/`TRASH` moves the message there (two of them at
once is 400); removing the current folder label archives it (removing `TRASH` restores it to
where it was trashed from); `DRAFT` cannot be added or removed (400); adding `SENT` to mail this
account did not send is 403. A `Label_...` id that spells a system label name (`SENT`, `DRAFT`,
`INBOX`, `ARCHIVE`, `UNREAD`, ...) is refused like `DRAFT` (400, before any write, recorded as a
refused `label`; in `insert`, a refused `insert`); elsewhere (list filters, `labels.get`) such
an id names no label (404). Changes that are already
in effect cost nothing (also `trash` of a trashed and `untrash` of a non-trashed message). Otherwise the writes are, in order: folder move, user labels, flags,
each a separate budgeted write; if a later write fails the earlier ones stay applied.

User labels exist when at least one message carries them, or after `labels.create`. The
engine stores labels only on messages, so a created but never applied label and the cosmetic
settings live in an **adapter-side registry**:

- it assumes a **single server process** and is lost on restart;
- it is kept per (run, mailbox) and **emptied when the run is reset** after its entries were
  recorded (labels never survive a reset);
- at most 1000 registered labels per (run, mailbox) (400 beyond);
- `labels.create` (and settings-only updates) are **not** engine writes: not budgeted, not in
  the audit trail;
- settings are validated strictly: `messageListVisibility` in `show|hide`,
  `labelListVisibility` in `labelShow|labelShowIfUnread|labelHide`, `color` exactly
  `{textColor, backgroundColor}` as `#rrggbb` (400 otherwise); other keys are ignored.

Renaming or deleting a label rewrites it on every message carrying it (one `label` write per
message). This is **not atomic**: if a write fails (budget, run ended), the messages already
rewritten keep the change and the error is returned; repeat the call to finish. Names are
unique case-insensitively (409 on conflict); system names are reserved (Gmail's, any case,
and `DRAFTS`/`ARCHIVE`: the same list every interface refuses as labels).

## Search (`q`)

| Syntax | Meaning |
|---|---|
| `word`, `"exact phrase"` | case-insensitive substring of subject, plain-text body or sender address |
| `from:` `to:` `cc:` `bcc:` | substring of address or display name; `me` = the mailbox address |
| `subject:` | substring of the subject |
| `label:` | user label (case-insensitive, `/` and spaces match `-`) or `inbox` `sent` `drafts` `trash` `unread` `read` `starred` |
| `in:` | `inbox` `sent` `drafts` `trash` `archive` `anywhere` (`spam` matches nothing) |
| `is:` | `unread` `read` `starred` `unstarred` `draft` |
| `has:` | `attachment` `userlabels` `nouserlabels` |
| `filename:` | substring of an attachment filename (`filename:pdf`) |
| `after:` `before:` (`newer:` `older:`) | `YYYY/MM/DD`, `YYYY-MM-DD` or epoch seconds, **UTC** |
| `older_than:` `newer_than:` | `N` + `d`/`m`/`y` (m = 30 days, y = 365 days) relative to the run's **virtual** time |
| `rfc822msgid:` | the Message-ID (with or without `<>`) |
| `op:(a b)` | `op:a op:b` |
| `a b`, `a AND b` | both |
| `a OR b`, `{a b}` | either |
| `-a` | not |
| `( ... )` | grouping |

Other Gmail operators (`larger:`, `size:`, `category:`, `list:`, `deliveredto:`, `AROUND`, ...)
and unsupported values (`is:important`, `in:chats`, `has:drive`, ...) are refused with 400
`INVALID_REQUEST` and a message naming the problem. Unknown `word:value` tokens are plain text.

Trash is excluded from `messages.list`, `threads.list` and `drafts.list` unless
`includeSpamTrash=true`, `labelIds` contains `TRASH`, or the query names `in:trash`,
`in:anywhere` or `label:trash` (Gmail behaviour). There is no spam folder.

Limits: `q` at most 2048 characters and 32 levels of nesting (parentheses, braces,
negation); dates between 1970 and 2100; `older_than`/`newer_than` at most 100 years; digits
must be ASCII. `fields` at most 1024 characters and 32 levels. Violations are 400.

Evaluation: the query is parsed first (bad queries fail before any read). Free text is matched
with the engine's search (so the scenario's `search` permission applies and the call is audited
as `search`); a positive top-level phrase's engine hits are the candidate set, otherwise the
mailbox is listed and filtered in the adapter. When the filters require exactly one folder
(`labelIds` with one of `INBOX`/`SENT`/`DRAFT`/`TRASH`, or a top-level `in:<folder>`), only that
folder is listed. Mailboxes are small; there is no index.
Results are newest first by internal date (received time, else creation time).

## history.list

`historyId` is the run's event sequence. `startHistoryId` must be a non-negative integer
(ASCII digits, at most 2^62).
Each mailbox change after it becomes one history record with `messages: [{id, threadId}]`:

| Engine change | Record field | `historyTypes` |
|---|---|---|
| delivered / sent / inserted / draft created / copied | `messagesAdded` (with current `labelIds`) | `messageAdded` |
| deleted | `messagesDeleted` | `messageDeleted` |
| labels / flags changed, moved | **no `labelsAdded`/`labelsRemoved` fields at all** (the engine change feed carries no delta): only `messages`; clients must refetch the listed message | `labelAdded`, `labelRemoved` |

A run reset after `startHistoryId` answers 404 (as Gmail does for a stale history id): perform a
full sync. `labelId` filters on the message's current labels (deleted messages never match).
Paging follows `nextPageToken`; the response `historyId` is the latest sequence.

## Message resources

- `labelIds`, `snippet` (whitespace-collapsed text, 200 chars, not HTML-escaped),
  `historyId` (latest sequence at response time, not the message's own last change),
  `internalDate` (ms), `sizeEstimate` (raw size).
- `full`: the MIME tree of the stored RFC 822 message (`partId` `""`, `"0"`, `"0.1"`, ...),
  decoded header values, body `data` as base64url **with padding**; attachments carry an
  `attachmentId` instead of data.
- `raw`: the stored RFC 822 bytes (Bcc is never in the wire copy).
- `metadata`: top-level headers, filtered by `metadataHeaders` (case-insensitive).

Request `raw` fields accept base64url with or without padding (the standard alphabet is
tolerated). HTML bodies are reduced to text (snippets, HTML-only messages) by a linear tag
stripper over at most 256 KB (8 KB for snippets); script/style content is dropped. Multipart
upload metadata is limited to 64 KB.

## Errors

Google's shape, plus a `google.rpc.ErrorInfo` detail whose `reason` is the neutral code:

```json
{"error": {"code": 404, "message": "...", "status": "NOT_FOUND",
           "errors": [{"message": "...", "domain": "global", "reason": "notFound"}],
           "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "NOT_FOUND", "domain": "global", "metadata": {}}]}}
```

| Neutral code | HTTP | `errors[].reason` | `status` |
|---|---|---|---|
| `INVALID_REQUEST` (incl. bad query, bad `fields`, bad page token, validation) | 400 | `invalidArgument` | `INVALID_ARGUMENT` |
| `UNAUTHENTICATED` | 401 (+ `WWW-Authenticate: Bearer`) | `authError` | `UNAUTHENTICATED` |
| `PERMISSION_DENIED` | 403 | `forbidden` | `PERMISSION_DENIED` |
| `NOT_FOUND` (incl. unknown label ids) | 404 | `notFound` | `NOT_FOUND` |
| `CONFLICT` | 409 | `conflict` | `ABORTED` |
| `RUN_NOT_ACTIVE` | 409 | `failedPrecondition` | `FAILED_PRECONDITION` |
| `EXTERNAL_DELIVERY_BLOCKED` | 400 | `failedPrecondition` | `FAILED_PRECONDITION` |
| `SIMULATION_LIMIT_EXCEEDED` (ErrorInfo reason `QUOTA_EXCEEDED`), `RATE_LIMITED` | 429 | `rateLimitExceeded` | `RESOURCE_EXHAUSTED` |
| `UNSUPPORTED_OPERATION` | 501 | `notImplemented` | `UNIMPLEMENTED` |
| `UPSTREAM_UNAVAILABLE` | 503 | `backendError` | `UNAVAILABLE` |
| unexpected fault | 500 (no detail) | `backendError` | `INTERNAL` |

Engine budget and run-state errors read neutrally to agents (`api/agent_errors.py`, the same on every interface): `SIMULATION_LIMIT_EXCEEDED` is shown as `QUOTA_EXCEEDED` with the message "action limit reached" and no details; `RUN_NOT_ACTIVE` keeps its code with the message "mailbox is not available". Operators (controller/evaluator routes, actions, events) keep the engine codes and messages.

Note that google-api-python-client retries 429 and 5xx when `num_retries` is set; the budget
error (`QUOTA_EXCEEDED`, "action limit reached") is raised before the write and costs nothing to retry, but
retrying it never succeeds. The per-run caps (reads, agent events, labels per message; see the
README) answer the same 429, also before anything is written: the message a send, insert or
modify returns is read back without counting against the caps, so a 429 never follows a
committed write. Only a label-cap refusal is a failed write that costs budget. Request bodies over the server's size cap are refused by the shared
middleware with the REST error envelope (400), not the Gmail shape.

## Known deviations (summary)

- Sandbox bearer keys instead of Google OAuth; no token refresh.
- `includeSpamTrash` only governs Trash (no spam folder); `SPAM`/`IMPORTANT`/`CATEGORY_*` and
  `CHAT` are not modelled.
- Unknown label ids answer 404 `notFound` (Gmail: 400 "Invalid label").
- User label ids derive from names and change on rename; unapplied labels live in the server
  process only; `labels.create` is not an audited write.
- Extra system labels `ANSWERED` / `DELETED`.
- No attachments when composing; no send-as aliases.
- History records for label/flag/folder changes carry no label delta.
- Free-text search is substring-based over subject, text body and sender address; dates are UTC.
- `historyId` on messages and threads is the mailbox's latest sequence.
- Not supported: settings, watch/push notifications, batch endpoint, resumable uploads,
  `messages.get` `format=metadata` of nested parts.
