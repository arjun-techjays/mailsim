# IMAP4rev1 + SMTP compatibility profile

mailsim exposes each run's **agent mailbox** over two inbound listeners so agents and mail
clients built on IMAP/SMTP can be tested unmodified. Both are **servers only**: there is no code
path that connects to another mail server. Accepted SMTP mail becomes simulator state through
the agent facade (`AgentMailbox`), exactly like a REST `POST /messages`, and external recipients
follow the scenario's delivery policy.

Code: `src/mailsim/protocols/` (`imap.py`, `imapwire.py`, `imapmime.py`, `smtp.py`, `auth.py`,
`tls.py`, `server.py`). Tests: `tests/protocols/`, plus the `imap_smtp` driver in the
cross-adapter contract suite (`tests/contract/drivers.py`).

## Credentials

| Field    | Value                                                                        |
|----------|------------------------------------------------------------------------------|
| username | the agent mailbox address (e.g. `finance@example.test`), case-insensitive    |
| password | an **agent** API key (`msk_...`) bound to that run + mailbox                  |

Get the key from `POST /v1/runs/{run_id}/agent-credentials` (controller key); the response
carries `api_key` and `mailbox_address`. Controller/evaluator keys, keys of another mailbox,
revoked keys and keys of finished runs are refused. There is **no unauthenticated mode**:
`MAILSIM_LOCAL_NO_AUTH` applies to the dashboard/control plane only and is ignored here.

Failures: IMAP `NO [AUTHENTICATIONFAILED]` (bad/unknown key or username),
`NO [AUTHORIZATIONFAILED] PERMISSION_DENIED: ...` (valid key, mailbox not the agent's),
`NO [UNAVAILABLE] RUN_NOT_ACTIVE` (run not started / reset in progress). Three failures close
the connection. SMTP answers `535 5.7.8` (with the engine code when the engine refused).

Every IMAP/SMTP command runs as the `agent` actor: reads are free but audited (`read` actions),
writes go through the same permission, mailbox and budget guards as REST and count against
`max_agent_actions`. Denials are audited by the engine.

Sessions re-check their key at most every 5 seconds: once the key is revoked or expired, or the
run reaches a terminal state, IMAP answers `* BYE [UNAVAILABLE] credentials revoked or expired`
and SMTP `421 4.7.0`, and the connection closes.

## Running

`serve` starts both listeners **with the API** (same host; IMAP 1143, SMTP 1587 by default), so
one command brings everything up. `serve protocols` still runs the listeners alone.

```bash
python -m mailsim.serve serve                          # API :8000 + IMAP :1143 + SMTP :1587
python -m mailsim.serve serve --imap-port 2143 --smtp-port 2587 --tls-self-signed
python -m mailsim.serve serve --no-protocols           # API only
python -m mailsim.serve protocols                      # listeners only (127.0.0.1:1143 / :1587)
python -m mailsim.serve protocols --tls-cert c.pem --tls-key k.pem --host 0.0.0.0
```

Uses `MAILSIM_DATABASE_URL` / `MAILSIM_BLOB_DIR` like `serve`. A non-loopback `--host` is refused
without TLS unless `--allow-insecure-remote` is given; under `serve` that refusal, a port that
is already in use or an unreadable certificate is printed on stderr and the API keeps running
with the listeners marked not running (`GET /v1/system/status`, `GET /v1/runs/{id}/connection`).
Local no-auth mode (`serve --local-no-auth`) never applies to the listeners: they always require
an agent key. Actions that come through the listeners are recorded with `interface` `imap` or
`smtp` (`GET /v1/runs/{id}/activity`). The dashboard's connection test
(`POST /v1/runs/{id}/connection-tests`) checks a key through the same LOGIN/AUTH code path
without opening a connection; that check is listed in the activity with the actor
`connection_test`, not as an agent action.

The listeners take an engine object (this package may not import `mailsim.engine.core`), so the
composition root starts them:

```python
from mailsim.protocols.server import run, ProtocolServers

run(engine, host="127.0.0.1", imap_port=1143, smtp_port=1587, tls=False)  # blocks
with ProtocolServers(engine, imap_port=0, smtp_port=0) as servers:  # background thread
    print(servers.imap_port, servers.smtp_port)
```

`tls=True` generates a throw-away self-signed certificate (via `cryptography`); pass
`tls_context=mailsim.protocols.tls.server_context(cert_pem, key_pem)` for your own. Bind to
loopback (the default) or to the Compose `sandbox` network only; the listeners never need
egress. A non-loopback `host` is refused unless TLS is configured or
`allow_insecure_remote=True` is passed. Each listener accepts at most 64 concurrent connections
(`max_connections`; IMAP answers `* BYE`, SMTP `421`; a STARTTLS upgrade still counts as one
connection). Engine calls run on a dedicated thread pool sized for every connection of both
listeners.

### Connecting

imaplib / smtplib:

```python
import imaplib, smtplib

imap = imaplib.IMAP4("127.0.0.1", 1143)
imap.login("finance@example.test", API_KEY)  # or imap.starttls() first when --tls
imap.select("INBOX")
typ, data = imap.uid("FETCH", "1:*", "(FLAGS ENVELOPE BODY.PEEK[])")

with smtplib.SMTP("127.0.0.1", 1587) as s:
    s.login("finance@example.test", API_KEY)  # s.starttls() first when --tls
    s.sendmail("finance@example.test", ["vendor@example.test"], raw_message_bytes)
```

Thunderbird: *Account Settings -> Add Mail Account -> Configure manually*. IMAP server
`127.0.0.1`, port `1143`, connection security *None* (or *STARTTLS* with `--tls`; accept the
self-signed certificate), authentication *Normal password*. SMTP server `127.0.0.1`, port
`1587`, same security, *Normal password*. Username = mailbox address, password = agent key.
Untick *Place a copy in: Sent* (Copies & Folders): every SMTP submission is already filed in
`Sent`, and an APPEND to `Sent` is refused (`NO [NOPERM]`). Keep drafts in a local folder
(*Drafts & Templates*): an APPEND to `Drafts` is refused too (create drafts through another
interface).

## IMAP profile

`CAPABILITY`: `IMAP4rev1 LITERAL+ UIDPLUS MOVE UNSELECT SPECIAL-USE NAMESPACE ID OBJECTID
CHILDREN` plus, before login, `SASL-IR AUTH=PLAIN` and (with TLS, until negotiated) `STARTTLS`.
Not offered: IDLE, CONDSTORE/QRESYNC, ENABLE, LIST-EXTENDED, COMPRESS, IMAP4rev2, NOTIFY.

| State            | Commands                                                                    |
|------------------|-----------------------------------------------------------------------------|
| any              | `CAPABILITY`, `NOOP`, `LOGOUT`, `ID`                                        |
| not authenticated| `LOGIN`, `AUTHENTICATE PLAIN` (continuation or SASL-IR; `*` cancels), `STARTTLS` |
| authenticated    | `SELECT`, `EXAMINE`, `LIST`, `LSUB`, `STATUS`, `APPEND`, `NAMESPACE`, `SUBSCRIBE`, `UNSUBSCRIBE`; `CREATE`/`DELETE`/`RENAME` answer `NO [CANNOT] UNSUPPORTED_OPERATION` |
| selected         | `CHECK`, `CLOSE`, `UNSELECT`, `EXPUNGE`, `SEARCH`, `FETCH`, `STORE`, `COPY`, `MOVE`, `UID FETCH/SEARCH/STORE/COPY/MOVE/EXPUNGE` |

**Folders** (fixed system set, delimiter `/`): `INBOX`, `Sent` (`\Sent`), `Drafts` (`\Drafts`),
`Trash` (`\Trash`), `Archive` (`\Archive`), all `\HasNoChildren`. Names match case-insensitively.
`LIST`/`LSUB` support `*` and `%` (matched without regular expressions; reference + pattern
at most 255 characters); LSUB lists every folder (all are subscribed).

**SELECT/EXAMINE** returns `FLAGS`, `EXISTS`, `RECENT 0`, `[UNSEEN n]`, `[PERMANENTFLAGS]`
(`\*` = keywords allowed; `()` for EXAMINE), `[UIDVALIDITY]`, `[UIDNEXT]`, `[MAILBOXID]`, and
`[READ-WRITE]`/`[READ-ONLY]`. A failed SELECT leaves no mailbox selected.

**UIDs and sequence numbers.** UIDs, `UIDNEXT` and `UIDVALIDITY` are the engine's per-folder
values: stable across sessions and EXPUNGE, strictly increasing, and a moved message gets a new
UID in its destination (`COPYUID` reports the mapping). Sequence numbers are the UID order of
the session's view. `UIDVALIDITY` changes when the run is reset; a session that observes the
change receives `* BYE [UNAVAILABLE] UIDVALIDITY changed` and must reconnect. `*` and ranges
follow RFC 3501 (e.g. `UID FETCH 99:*` returns the highest UID even if it is below 99).

**Change detection.** A session keeps its folder list and the selected folder's listing and
asks the engine change feed (`list_changes`, one audited `read`) whether anything changed in the
mailbox; only then does it re-list. An idle `NOOP` therefore records one read action. A run
reset seen on the feed ends a selected session as described above.

**Unsolicited updates.** New messages are always announced
with `EXISTS`; flag changes made by other sessions/adapters are sent as `FETCH (UID n FLAGS
(...))`; removed messages produce `EXPUNGE` on `NOOP`, `CHECK`, `EXPUNGE`, `MOVE`, `COPY`,
`APPEND` and every `UID` command, never during non-UID `FETCH`/`STORE`/`SEARCH` (RFC 3501
7.4.1) - until then they stay in the sequence map and are skipped.

**FETCH** items: `UID`, `FLAGS`, `INTERNALDATE`, `RFC822.SIZE`, `ENVELOPE`, `BODYSTRUCTURE`,
`BODY`, `RFC822`, `RFC822.HEADER`, `RFC822.TEXT`, `EMAILID`, `THREADID`, macros `ALL`/`FAST`/
`FULL`, and `BODY[section]<start.len>` / `BODY.PEEK[...]` with sections `""`, `HEADER`,
`TEXT`, `HEADER.FIELDS (..)`, `HEADER.FIELDS.NOT (..)`, `MIME`, part numbers (`1`, `2.1`,
`2.HEADER` for `message/rfc822` parts, ...). Sections are byte-exact slices of the stored MIME
(never re-serialized); ENVELOPE keeps header values verbatim (encoded-words, 8-bit text as a
literal). FETCH modifiers (`CHANGEDSINCE`) answer `BAD UNSUPPORTED_OPERATION`. Message bytes
come from `AgentMailbox.get_raw` (one audited read per message, cached per session up to
64 MiB); FETCH responses are streamed to the client message by message.

**`\Seen` (RFC 3501 6.4.5).** A non-PEEK `BODY[...]`, `RFC822` or `RFC822.TEXT` fetch in a
read-write `SELECT` marks the message seen through `AgentMailbox.mark_seen` (recorded as a
`read`, never budgeted, allowed whenever reading is) and adds `FLAGS` to that FETCH response.
`BODY.PEEK[...]`, `RFC822.HEADER` and anything under `EXAMINE` never set it.

**SEARCH** keys: `ALL`, `ANSWERED`, `DELETED`, `DRAFT`, `FLAGGED`, `SEEN`, their `UN*` forms,
`NEW`/`RECENT` (never match), `OLD`, `KEYWORD`, `UNKEYWORD`, `FROM`, `TO`, `CC`, `BCC`,
`SUBJECT`, `BODY`, `TEXT`, `HEADER field value`, `BEFORE`/`ON`/`SINCE` (internal date),
`SENTBEFORE`/`SENTON`/`SENTSINCE` (Date header), `LARGER`, `SMALLER`, `UID set`, sequence sets,
`NOT`, `OR`, parenthesised lists, and RFC 8474 `EMAILID`/`THREADID`. `CHARSET` `US-ASCII` or
`UTF-8` (`NO [BADCHARSET]` otherwise). Matching is case-insensitive substring on decoded text.

**Flags.**

| IMAP          | engine flag | notes                                                        |
|---------------|-------------|--------------------------------------------------------------|
| `\Seen`       | `SEEN`      |                                                              |
| `\Answered`   | `ANSWERED`  | also set by the engine when the agent replies               |
| `\Flagged`    | `FLAGGED`   |                                                              |
| `\Deleted`    | `DELETED`   | removed by `EXPUNGE`/`CLOSE`/`UID EXPUNGE` (engine `delete`) |
| `\Draft`      | `DRAFT`     | marks real drafts only: `+FLAGS`/`FLAGS` with it on a non-draft and `-FLAGS` on a draft answer `NO [NOPERM]`; `FLAGS` without it keeps a draft's |
| `\Recent`     | -           | never set; ignored in STORE                                  |
| keyword `foo` | user label `foo` | case-insensitive; labels that are not IMAP atoms are not shown; system label names (`Sent`, `Draft`, `Inbox`, ...) answer `NO [CANNOT]` |

STORE with several kinds of change issues one `flag` and/or one `label` engine write per message
(only when something changes). `.SILENT` suppresses the FETCH echo. `UID EXPUNGE` removes
each listed `\Deleted` message with its own engine `delete` (one action per message), whereas
`EXPUNGE`/`CLOSE` use a single engine `expunge`.

**Objects.** `EMAILID` is the engine message id (unchanged by MOVE; COPY creates a new id),
`THREADID` the engine thread id, `MAILBOXID` the engine folder id.

**APPEND** stores the literal verbatim via `insert_message` (Gmail `insert` semantics: no
delivery), then applies the given flags/keywords; answers `[APPENDUID]`. `Sent` and `Drafts`
hold only what this account sent or drafted, so APPEND to them answers `NO [NOPERM]`, and so
does `\Draft` in the APPEND flags (checked before anything is stored). An appended message is
never sent mail: it can't be moved or copied into `Sent` either.

**COPY/MOVE** use engine `copy`/`move` and answer `COPYUID`; a missing destination answers
`NO [TRYCREATE]`.

**Errors.** Syntax errors answer `BAD`; engine errors answer `NO [code] ENGINE_CODE: message`
(`NOT_FOUND` -> `NONEXISTENT`, `PERMISSION_DENIED` -> `NOPERM`, `RUN_NOT_ACTIVE` ->
`UNAVAILABLE`, `SIMULATION_LIMIT_EXCEEDED`/`RATE_LIMITED` -> `LIMIT`, `INVALID_REQUEST`/
`UNSUPPORTED_OPERATION` -> `CANNOT`, `CONFLICT` -> `INUSE`). The budget error reads
`NO [LIMIT] QUOTA_EXCEEDED: action limit reached`; a run that is not active reads
`RUN_NOT_ACTIVE: mailbox is not available` (also at LOGIN).

**Framing limits.** Command line 64 KiB (`* BYE` beyond it); synchronizing literals up to
25 MiB + 64 KiB (larger ones answer `NO [TOOBIG]` without the `+` continuation; an oversized
`LITERAL+` literal closes the connection); autologout after 30 minutes idle. Plaintext pipelined
after `STARTTLS` closes the connection (command-injection guard). Before login, literals are
limited to 8 KiB, a whole command to 16 KiB, and the idle limit is 60 seconds. A client that
stops reading for 60 seconds while responses are pending is disconnected. Parenthesised lists and SEARCH `NOT`/`OR`
nesting are capped at 32 levels; numbers in sequence sets and partial ranges at 10 digits.

## SMTP profile

aiosmtpd's `SMTP` protocol with a sandbox handler (the relaying `aiosmtpd.handlers` module is
banned). `EHLO` advertises `SIZE 26214400`, `8BITMIME`, `AUTH PLAIN LOGIN`, and `STARTTLS` when
TLS is configured.

| Step        | Behaviour                                                                     |
|-------------|-------------------------------------------------------------------------------|
| `AUTH`      | required before `MAIL` (`530 5.7.0` otherwise); PLAIN (an authorization identity other than the login is refused) or LOGIN, checked in a worker thread (never on the event loop); `535 5.7.8` on failure; at most 3 `AUTH` per connection (then `421`) and 100 of any command |
| `MAIL FROM` | must be the authenticated mailbox (`553 5.7.1` otherwise); null sender refused |
| `RCPT TO`   | per recipient: simulated mailbox -> `250`; outside the simulation -> `550 5.7.1 <addr>: relay not permitted` when the run is in `deny` mode or the agent is prohibited from `external_delivery`, otherwise a plain `250` (the `sink`/`event` policy handles it, never delivered; the mode is not revealed); bad syntax `501 5.1.3`; more than 100 -> `452 4.5.3` |
| `DATA`      | parsed with `mailsim.mime` (25 MiB limit: `552`); `250 2.0.0 OK queued as <Message-ID> id=<message id>` |

Partial rejection works the usual way: rejected recipients get their own 5xx and the message is
committed for the accepted ones only (smtplib's `sendmail` returns them in its refused dict).

**What a submission becomes.** The envelope decides who receives the message: header `To`/`Cc`
addresses that are envelope recipients stay `To`/`Cc`, other envelope recipients become `Bcc`.

1. `In-Reply-To` (else the `References` entries, newest first) resolves to a message in the
   mailbox via `find_by_message_id` -> ONE engine `reply` carrying the client's To/Cc/Bcc,
   Subject, text and HTML (scenario policy, budget, `send_reply` triggers and `\Answered` on the
   original apply exactly as for any reply).
2. Reply headers that resolve to nothing -> engine `draft` + `send` of that draft (two actions,
   both must be allowed; reply headers and threading preserved).
3. Otherwise -> engine `send` (one action).

Engine errors map to replies carrying the engine code: `EXTERNAL_DELIVERY_BLOCKED`/
`PERMISSION_DENIED` `550 5.7.1`, the action budget `452 4.5.3 quota exceeded` (no code),
`RUN_NOT_ACTIVE` `554 5.3.2 RUN_NOT_ACTIVE: mailbox is not available` (and `535 5.7.8` with the
same text at AUTH), `INVALID_REQUEST` `554 5.6.0`, `RATE_LIMITED` `451 4.7.1`, others `451 4.3.0`.
Unexpected failures answer `451 4.3.0 internal error`; details are only logged.

## Deviations (by design or pending engine support)

- **Message-ID is assigned by the engine.** The client's `Message-ID`, `Date` and other
  headers are replaced; the assigned `Message-ID` is returned in the `250` DATA reply.
- **No attachments over SMTP** (`554 5.6.1 attachments are not accepted on this submission
  path`): the agent send operations
  carry text/HTML only. APPEND accepts any MIME.
- **Replies whose headers resolve to nothing** cost two actions (`draft` + `send`).
- **APPEND date-time** is accepted and ignored (internal date = virtual clock).
- **Folders are fixed**; no user folders, no hierarchy.
- **No IDLE / CONDSTORE**: poll with `NOOP` (one audited `read` per poll when idle).
- **SEARCH** matches decoded text by substring (not RFC 3501's exact octet rules for every
  charset); `NEW`/`RECENT` never match because `\Recent` is never set.
- **`EMAILID` after COPY** is a new id (RFC 8474 says SHOULD keep it).
- STARTTLS is optional on both listeners (no `LOGINDISABLED`, AUTH allowed in clear) because
  they are meant for loopback / the internal sandbox network (non-loopback binds need TLS).

## Engine access notes

The listeners use only `AgentMailbox` (`mailbox_info` for the login address, `get_raw` for
message bytes with a rebuild via `mailsim.mime.build` when none is stored, `find_by_message_id`
for replies, `mark_seen`, `list_changes`), plus one read-only store lookup the facade does not
offer: the run's internal addresses and external-delivery decision, reduced to allow/deny and
re-read for every SMTP transaction (so a run reset is picked up). It reaches neither scenario specs nor answer keys.

## What the agent sees

Greetings and replies are neutral: the SMTP banner is `220 <hostname> ESMTP ready`, the IMAP
greeting `* OK [CAPABILITY ...] IMAP4rev1 ready`, `ID` answers `("name" "IMAP" "vendor"
"IMAP")`, and no reply text mentions the simulation or the run's external-delivery mode.
Engine budget and run-state errors read neutrally to agents (`api/agent_errors.py`, the same on every interface): `SIMULATION_LIMIT_EXCEEDED` is shown as `QUOTA_EXCEEDED` with the message "action limit reached" and no details; `RUN_NOT_ACTIVE` keeps its code with the message "mailbox is not available". Operators (controller/evaluator routes, actions, events) keep the engine codes and messages.
