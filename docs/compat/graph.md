# Microsoft Graph v1.0 mail compatibility profile

The sandbox serves a **defined subset** of Microsoft Graph v1.0 mail (spec §5.2) at
`/graph/v1.0`, backed by the same canonical run state as the neutral REST API, Gmail, IMAP/SMTP
and MCP (spec §5.5). It is not a clone of Exchange Online. Everything outside this profile
fails explicitly with `501 ErrorNotSupported`. Nothing is ever delivered outside the sandbox.

Code: `src/mailsim/adapters/graph/` (router: `mailsim.adapters.graph.api:router`, mounted by
`mailsim.serve.mount_adapters`). Tests: `tests/adapters/graph/` (raw HTTP, OData unit tests, and
fidelity tests with the official `msgraph-sdk`), plus `GraphDriver` in the cross-adapter
contract suite (`tests/contract/`).

## Authentication and identity

- Every request needs `Authorization: Bearer msk_...`, a sandbox **agent** key (issued with
  `POST /v1/scenarios/{key}/runs` returns one; `POST /v1/runs/{run_id}/agent-credentials` issues a new one). The key is bound to one
  run and one mailbox. There is no OAuth / Entra ID flow, and no local no-auth mode: the Graph
  adapter ignores `MAILSIM_LOCAL_NO_AUTH` and always requires an agent key.
- Missing, malformed, unknown, expired or revoked keys answer `401 InvalidAuthenticationToken`
  (with `WWW-Authenticate: Bearer`). Controller and evaluator keys answer `403 ErrorAccessDenied`.
- `/me/...` is the key's mailbox. `/users/{id}/...` is accepted only when `{id}` names that same
  mailbox: its address (case-insensitive, e.g. `finance@example.test`), its mailbox id, or its
  Graph user id (the `id` from `GET /me`). Any other user answers `403 ErrorAccessDenied`.
- Every call goes through the agent facade, so the scenario's agent permissions, run state,
  mailbox binding and action budget apply, and every call is audited as an agent action.

## Supported operations

Every path below exists under both `/graph/v1.0/me` and `/graph/v1.0/users/{id}`.

| Operation | Method and path | Status |
|---|---|---|
| Profile | `GET /me` (`id`, `displayName`, `mail`, `userPrincipalName`) | 200 |
| List messages | `GET /messages`, `GET /mailFolders/{folder}/messages` | 200 |
| Count messages | `GET /messages/$count`, `GET /mailFolders/{folder}/messages/$count` (`$filter`, `$search`) | 200 `text/plain` |
| Get message | `GET /messages/{id}` (`$select`, `$expand`) | 200 |
| MIME content | `GET /messages/{id}/$value` | 200 |
| Create draft | `POST /messages` | 201 |
| Update | `PATCH /messages/{id}` | 200 |
| Delete | `DELETE /messages/{id}` (to Deleted Items; from Deleted Items: removed) | 204 |
| Permanent delete | `POST /messages/{id}/permanentDelete` | 204 |
| Move / copy | `POST /messages/{id}/move`, `POST /messages/{id}/copy` (`destinationId`) | 201 |
| Send new mail | `POST /sendMail` (`message`, `saveToSentItems`) | 202 |
| Send draft | `POST /messages/{id}/send` | 202 |
| Reply / reply all / forward | `POST /messages/{id}/reply`, `/replyAll`, `/forward` | 202 |
| Draft reply / reply all / forward | `POST /messages/{id}/createReply`, `/createReplyAll`, `/createForward` | 201 |
| Folders | `GET /mailFolders`, `GET /mailFolders/{folder}`, `GET /mailFolders/{folder}/childFolders` (always empty) | 200 |
| Attachments | `GET /messages/{id}/attachments`, `GET /messages/{id}/attachments/{aid}`, `GET .../{aid}/$value` | 200 |
| Change tracking | `GET /mailFolders/{folder}/messages/delta` (Graph), `GET /messages/delta` (mailbox-wide extension); the `delta()` function form is accepted too | 200 |

Action parameters are case-insensitive, as in Graph (the SDK sends `Message`, `Comment`,
`DestinationId`, `SaveToSentItems`, `ToRecipients`).

### Folders

| Well-known name | Display name | Engine folder |
|---|---|---|
| `inbox` | Inbox | inbox |
| `sentitems` | Sent Items | sent |
| `drafts` | Drafts | drafts |
| `deleteditems` | Deleted Items | trash |
| `archive` | Archive | archive |

`{folder}` and `destinationId` take a well-known name (case-insensitive) or a folder `id`.
Other well-known names (`junkemail`, `outbox`, `msgfolderroot`, ...) answer `404
ErrorItemNotFound`. Folders carry `totalItemCount`, `unreadItemCount`, `childFolderCount` (0),
`parentFolderId` (a synthetic root id), `isHidden` (false) and `sizeInBytes` (0). Creating,
renaming or deleting folders is not supported.

## Message resource

| Graph property | Source |
|---|---|
| `id` | `provider_ids.graph_message_id`; for a draft, the stable `provider_ids.graph_draft_id` |
| `conversationId` | `provider_ids.graph_thread_id` |
| `internetMessageId` | RFC `Message-ID` (with angle brackets) |
| `subject` | subject |
| `body` | `{contentType: html, content}` when the message has an HTML part, else `text`; `Prefer: outlook.body-content-type="text"\|"html"` converts (and sets `Preference-Applied`) |
| `bodyPreview` | first 255 characters of the text body, whitespace collapsed |
| `from`, `sender` | From (`name` defaults to the address) |
| `toRecipients`, `ccRecipients`, `bccRecipients` | To / Cc / Bcc (`{emailAddress: {name, address}}`) |
| `receivedDateTime` | delivery time (`received_at`), else the `Date` header |
| `sentDateTime` | the `Date` header; `null` for drafts |
| `createdDateTime`, `lastModifiedDateTime` | the `Date` header (no separate modification time is kept) |
| `isRead` | `SEEN` flag |
| `isDraft` | draft |
| `flag.flagStatus` | `flagged` when the `FLAGGED` flag is set, else `notFlagged` |
| `categories` | the message's user labels (system folder labels and `UNREAD` are not categories) |
| `hasAttachments` | any non-inline attachment |
| `parentFolderId` | folder id |
| `internetMessageHeaders` | all parsed headers; only returned when `$select`ed (as in Graph) |
| `importance` `normal`, `inferenceClassification` `focused`, `isDeliveryReceiptRequested`/`isReadReceiptRequested` `false`, `replyTo` `[]` | constants |
| `changeKey`, `@odata.etag` | hash of the observable state (changes when the message changes) |

The `ANSWERED` flag is exposed the way Outlook does it, through the extended property
`PidTagLastVerbExecuted` (`Integer 0x1081`, value `102`): request it with
`$expand=singleValueExtendedProperties($filter=id eq 'Integer 0x1081')`, set or clear it by
PATCHing `singleValueExtendedProperties: [{"id": "Integer 0x1081", "value": "102"}]` (any
other value clears it). Other extended properties are not supported.

### Writes

- `POST /messages` (draft) and `sendMail` take `subject`, `body`, `toRecipients`,
  `ccRecipients`, `bccRecipients`. `from`/`sender` may only name the mailbox itself (else `403
  ErrorAccessDenied`, no send-as). Non-empty `attachments`, `replyTo`, `internetMessageHeaders`,
  `mentions`, `multiValueExtendedProperties` and an `importance` other than `normal` answer `501
  ErrorNotSupported`. Display names on recipients are not kept (the engine stores addresses).
- `PATCH /messages/{id}`: `isRead`, `flag.flagStatus` (`complete` clears the flag),
  `categories` (replaces the set), the replied marker above, and, on drafts only, `subject`,
  `body` and the recipient lists. Changes are applied as separate engine writes in the order
  content, flags, categories; if a later one fails the earlier ones stay applied (GET to see the
  state). Content changes on a non-draft answer `400 ErrorInvalidRequest`. A content change
  keeps the draft's flags (`isRead`, `flag`, replied marker) and categories unless the same PATCH
  changes them.
- Draft-to-sent: `POST /messages/{id}/send` removes the draft from Drafts, places the sent copy
  in Sent Items (new id) and delivers it to the simulated recipients. Sending a non-draft
  answers `409 ErrorIrresolvableConflict`.
- `reply`/`replyAll` (`comment`) and `forward` (`comment` + `toRecipients`, top level or inside
  `message`) run as exactly one engine `reply` / `forward` operation: threading headers,
  `ANSWERED` on the original, the scenario's `allowed_operations` for that operation, its
  `after_agent_action` triggers (e.g. `send_reply`) and one unit of action budget. The engine's
  forward keeps the original's attachments. A `message` object carrying any other property
  (subject, body, cc, ...) answers `501 ErrorNotSupported` instead of being emulated with draft +
  send; use `createReply`/`createForward`, PATCH the draft, then `send` it.
- `createReply` -> PATCH -> `POST .../send` is recorded by the engine as `draft`, `draft`, `send`
  operations (not `reply`): scenario permissions and `send_reply` triggers see a `send`.
- `createReply`/`createReplyAll` address the draft as Outlook does (sender; plus the other To/Cc
  for reply-all; never the mailbox itself), subject `RE: ...`, `In-Reply-To`/`References` set, so
  it threads into the same `conversationId`. `createForward` uses `FW: ...` with a forwarded
  header block in the body.

## OData query subset

| Option | Support |
|---|---|
| `$top` | 1..1000, default 10 (or `Prefer: odata.maxpagesize=N`); at most 50 per page with `$expand=attachments` (the rest via `@odata.nextLink`) |
| `$skip` | offset; `@odata.nextLink` carries `$skip` like Graph's message lists |
| `$skiptoken` | opaque token from a delta `@odata.nextLink`; also accepted on lists |
| `$count=true` | `@odata.count` |
| `$select` | any message property listed above; unknown names `400 ErrorInvalidRequest` |
| `$orderby` | `receivedDateTime`, `sentDateTime`, `createdDateTime`, `lastModifiedDateTime`, `subject`, `from/emailAddress/address`, `from/emailAddress/name`; `asc`/`desc`, comma-separated; default `receivedDateTime desc`. Ties keep the engine's (created, id) order |
| `$filter` | see below |
| `$search` | `"text"`: substring match over subject, text body and sender (not KQL); cannot be combined with `$filter`/`$orderby` |
| `$expand` | `attachments`; `singleValueExtendedProperties($filter=id eq '...')` |

`$filter` grammar: comparisons joined with `and` / `or` and parentheses.

- Booleans `isRead`, `isDraft`, `hasAttachments`: `eq`, `ne`.
- Dates `receivedDateTime`, `sentDateTime`, `createdDateTime`, `lastModifiedDateTime`: `eq ne gt
  ge lt le` with an unquoted DateTimeOffset (`2026-10-15T08:00:00Z`, or a date) or a quoted one.
- Strings `subject`, `conversationId`, `id`, `parentFolderId`, `internetMessageId`,
  `importance`, `inferenceClassification`, `flag/flagStatus`, `from/emailAddress/address`,
  `from/emailAddress/name`, `sender/emailAddress/address`, `sender/emailAddress/name`: `eq`, `ne`
  (case-insensitive, as Exchange), plus `startswith(subject, '...')`.
- Lambdas: `categories/any(c:c eq '...')`, `toRecipients|ccRecipients|bccRecipients/any(r:
  r/emailAddress/address eq '...')` (and `all`).
- `null` literals compare with `eq`/`ne`; ordering comparisons with null are false.

Limits: `$filter` up to 4096 characters and 32 nested parentheses/lambdas; integers
(`$top`, `$skip`, `odata.maxpagesize`) up to 10 ASCII digits; paging/delta tokens up to 256
characters. Beyond them, and for malformed queries, the answer is `400 ErrorInvalidRequest`; valid OData the sandbox does not implement
(`not`, `has`, `in`, `contains`, other properties or options such as `$apply`) answers `400
ErrorNotSupported`.

## Change tracking (delta)

- First call without a token returns every current message (engine order), paged by `$top` /
  `Prefer: odata.maxpagesize` (default 10, at most 500) with `@odata.nextLink` (`$skiptoken`),
  ending with `@odata.deltaLink` (`$deltatoken`) anchored at the run's event sequence taken
  before the read. Pages use the engine's keyset cursor over (created, id), so deleting or
  moving an already-returned message never hides a later one; anything that changes during the
  initial sync is reported again in the next round.
- Calling the `deltaLink` returns each message that changed since (deduplicated): the current
  message, or `{"id": ..., "@removed": {"reason": "deleted"}}` when it was deleted or (for a
  folder delta) is no longer in that folder. A round reads at most page-size change events (so
  at most that many message reads) and continues with `@odata.nextLink`.
- Tokens are opaque; `$select` is kept on the links. `$filter`/`$orderby` on delta are not
  supported.
- After a run reset, an older token answers `410 SyncStateNotFound`: restart without a token.

## Identifiers

| Entity | Graph id | Lifetime |
|---|---|---|
| message | `graph_message_id` = `AAMk` + base64url(sha256("graph:" + internal id)) | the internal message: survives flag/label/folder changes; `copy` and draft send create new ids |
| draft | `graph_draft_id` (same scheme over the engine's draft id) | stable across PATCH (the engine replaces the draft's internal message on each update) until it is sent or deleted |
| conversation | `graph_thread_id` | the thread; when threads merge, the surviving thread's id |
| folder | derived from (mailbox, system folder) | the run |
| attachment | derived from the internal attachment id | the message |
| user | derived from the mailbox id | the run |

Incoming ids resolve through `AgentMailbox.resolve_external_id("graph", ...)`, restricted to the
key's mailbox: ids of other mailboxes, internal ids (`msg_...`) and Gmail ids answer `404
ErrorItemNotFound`. Ids are deterministic for a given run seed and command sequence.

## Errors

Body: `{"error": {"code", "message", "innerError": {"request-id", "client-request-id",
"date"}}}`; `request-id` and `client-request-id` are also response headers (a well-formed
`client-request-id` / `x-request-id` from the client is echoed).

| Simulator code (spec §7.4) | HTTP | Graph `error.code` |
|---|---|---|
| (missing / invalid key) | 401 | `InvalidAuthenticationToken` |
| `INVALID_REQUEST` (incl. malformed OData, invalid JSON) | 400 | `ErrorInvalidRequest` |
| (valid but unsupported OData) | 400 | `ErrorNotSupported` |
| `NOT_FOUND` | 404 | `ErrorItemNotFound` |
| `PERMISSION_DENIED` | 403 | `ErrorAccessDenied` |
| `EXTERNAL_DELIVERY_BLOCKED` | 403 | `ErrorMessageSubmissionBlocked` |
| `CONFLICT` | 409 | `ErrorIrresolvableConflict` |
| `RUN_NOT_ACTIVE` | 412 | `ErrorPreconditionFailed` |
| `RATE_LIMITED` | 429 (+ `Retry-After: 1`) | `ErrorTooManyRequests` |
| `SIMULATION_LIMIT_EXCEEDED` | 429 | `ErrorQuotaExceeded` |
| `UNSUPPORTED_OPERATION` (incl. any path outside the profile) | 501 | `ErrorNotSupported` |
| `UPSTREAM_UNAVAILABLE` | 503 | `ErrorServiceUnavailable` |
| stale delta token | 410 | `SyncStateNotFound` |
| unexpected fault (no details) | 500 | `generalException` |

Each Graph code maps back to exactly one simulator code.

## Deviations from Microsoft Graph

- Authentication is a sandbox key, not an Entra ID access token; there is no `/oauth2` endpoint,
  no consent, no app-only vs delegated distinction, no throttling headers.
- `saveToSentItems: false` is accepted but a Sent Items copy is always kept.
- New attachments cannot be added (`POST .../attachments`, `attachments` on create/sendMail,
  upload sessions); only reading existing attachments is supported. All attachments are
  `fileAttachment` (no item or reference attachments). `createForward` drafts do not carry the
  original's attachments (the direct `forward` does).
- Replies and forwards do not quote the original as HTML; `createReply` bodies hold the comment.
- Subjects of direct `reply`/`forward` follow the engine (`Re:` / `Fwd:`); draft variants use
  Outlook's `RE:` / `FW:`.
- `lastModifiedDateTime` equals `createdDateTime`; `importance`, `inferenceClassification`,
  `replyTo` are constants; there is no `uniqueBody`, `webLink`, `conversationIndex`,
  `isDeliveryReceiptRequested` handling or `followupFlag` dates.
- `$search` is a substring search, not KQL. `$filter` is the subset above. `$skip` paging is
  offset-based over the state at request time (stable under a fixed state).
- `GET /me/messages/delta` (mailbox-wide) is an extension; Graph v1.0 offers delta per folder.
- A draft that was sent or deleted appears in delta as `@removed` under its last internal
  message's Graph id, not its draft id.
- `DELETE` of a message already in Deleted Items removes it (Exchange moves it to Recoverable
  Items, which is not modelled).
- No `$batch`, no change notifications (subscriptions), no mail rules, focused inbox, search
  folders, child folders, categories master list, mailbox settings or calendar.
- Each Graph request may record more than one agent read in the audit trail (e.g. id
  resolution + read); only writes count against the action budget.

## Cost and mailbox-size envelope

List, `$count`, `$search` and folder-count requests read the whole folder (or mailbox) through
the agent facade in engine pages of 500, then filter, order and page in memory; `$skip` paging
therefore costs one full scan per page. This is sized for simulated mailboxes: up to a few
thousand messages per mailbox the cost is a few engine reads per request. Much larger
mailboxes work but each list request grows linearly with the mailbox. Delta initial sync uses
keyset paging (one bounded read per page) and change rounds read at most one page of events.

## Pointing a client at the sandbox

1. Start the server (`python -m mailsim.serve serve`), create a run from a scenario and issue an
   agent key for it (`POST /v1/runs/{run_id}/agent-credentials` with a controller key).
2. Use `http://127.0.0.1:8000/graph/v1.0` as the Graph base URL and the agent key as the bearer
   token.

curl:

```sh
curl -H "Authorization: Bearer $AGENT_KEY" \
  "http://127.0.0.1:8000/graph/v1.0/me/mailFolders/inbox/messages?\$top=5&\$select=subject,from"
```

Python `msgraph-sdk` (no change to the SDK; the Azure Identity auth provider requires https, so
use kiota's bearer provider with a static token):

```python
import httpx
from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph.graph_request_adapter import options
from msgraph_core import GraphClientFactory

BASE = "http://127.0.0.1:8000/graph/v1.0"


class SandboxKey(AccessTokenProvider):
    def __init__(self, key):
        self.key = key

    async def get_authorization_token(self, uri, additional_authentication_context=None):
        return self.key

    def get_allowed_hosts_validator(self):
        return AllowedHostsValidator(["127.0.0.1:8000"])


http = GraphClientFactory.create_with_default_middleware(
    client=httpx.AsyncClient(base_url=BASE), options=options
)
adapter = GraphRequestAdapter(BaseBearerTokenAuthenticationProvider(SandboxKey(AGENT_KEY)), http)
adapter.base_url = BASE
client = GraphServiceClient(request_adapter=adapter)
messages = await client.me.mail_folders.by_mail_folder_id("inbox").messages.get()
```

What the SDK needs that the sandbox does not emulate: an Entra ID token (replaced by the static
sandbox key above), https for `AzureIdentityAuthenticationProvider` (use the bearer provider),
and `$batch` (`BatchRequestContent` is unsupported). The SDK's retry handler retries `429` and
`503` up to three times; `ErrorQuotaExceeded` (the run's action budget) will not clear on retry.
Verified operations: see `tests/adapters/graph/test_sdk.py` (profile, folders, list with
`$select`/`$orderby`/`$top`/`$count`/`$filter` and `PageIterator`, `/users/{upn}`, `$count`,
get, PATCH, attachments, MIME `$value`, sendMail, reply, forward, move, copy, delete,
permanentDelete, draft create/update/send, createReply, delta with `@removed`, and `ODataError`
mapping for 401/403/404/400/501).
