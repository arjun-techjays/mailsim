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

## Running

The listeners take an engine object (this package may not import `mailsim.engine.core`), so the
composition root starts them:

```python
from mailsim.protocols.server import run, ProtocolServers
run(engine, host="127.0.0.1", imap_port=1143, smtp_port=1587, tls=False)   # blocks
with ProtocolServers(engine, imap_port=0, smtp_port=0) as servers:          # background thread
    print(servers.imap_port, servers.smtp_port)
```

`tls=True` generates a throw-away self-signed certificate (via `cryptography`); pass
`tls_context=mailsim.protocols.tls.server_context(cert_pem, key_pem)` for your own. Bind to
loopback (the default) or to the Compose `sandbox` network only; the listeners never need
egress.

### Connecting

imaplib / smtplib:

```python
import imaplib, smtplib
imap = imaplib.IMAP4("127.0.0.1", 1143)
imap.login("finance@example.test", API_KEY)       # or imap.starttls() first when --tls
imap.select("INBOX")
typ, data = imap.uid("FETCH", "1:*", "(FLAGS ENVELOPE BODY.PEEK[])")

with smtplib.SMTP("127.0.0.1", 1587) as s:
    s.login("finance@example.test", API_KEY)       # s.starttls() first when --tls
    s.sendmail("finance@example.test", ["vendor@example.test"], raw_message_bytes)
```

Thunderbird: *Account Settings -> Add Mail Account -> Configure manually*. IMAP server
`127.0.0.1`, port `1143`, connection security *None* (or *STARTTLS* with `--tls`; accept the
self-signed certificate), authentication *Normal password*. SMTP server `127.0.0.1`, port
`1587`, same security, *Normal password*. Username = mailbox address, password = agent key.

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
`LIST`/`LSUB` support `*` and `%`; LSUB lists every folder (all are subscribed).

**SELECT/EXAMINE** returns `FLAGS`, `EXISTS`, `RECENT 0`, `[UNSEEN n]`, `[PERMANENTFLAGS]`
(`\*` = keywords allowed; `()` for EXAMINE), `[UIDVALIDITY]`, `[UIDNEXT]`, `[MAILBOXID]`, and
`[READ-WRITE]`/`[READ-ONLY]`. A failed SELECT leaves no mailbox selected.

**UIDs and sequence numbers.** UIDs, `UIDNEXT` and `UIDVALIDITY` are the engine's per-folder
values: stable across sessions and EXPUNGE, strictly increasing, and a moved message gets a new
UID in its destination (`COPYUID` reports the mapping). Sequence numbers are the UID order of
the session's view. `UIDVALIDITY` changes when the run is reset; a session that observes the
change receives `* BYE [UNAVAILABLE] UIDVALIDITY changed` and must reconnect. `*` and ranges
follow RFC 3501 (e.g. `UID FETCH 99:*` returns the highest UID even if it is below 99).

**Unsolicited updates.** Each command re-reads the folder. New messages are always announced
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
literal). FETCH modifiers (`CHANGEDSINCE`) answer `BAD UNSUPPORTED_OPERATION`.

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
| `\Draft`      | `DRAFT`     |                                                              |
| `\Recent`     | -           | never set; ignored in STORE                                  |
| keyword `foo` | user label `foo` | labels that are not valid IMAP atoms are not shown       |

STORE with several kinds of change issues one `flag` and/or one `label` engine write per message
(only when something changes). `.SILENT` suppresses the FETCH echo.

**Objects.** `EMAILID` is the engine message id (unchanged by MOVE; COPY creates a new id),
`THREADID` the engine thread id, `MAILBOXID` the engine folder id.

**APPEND** stores the literal verbatim via `insert_message` (Gmail `insert` semantics: no
delivery), then applies the given flags/keywords; answers `[APPENDUID]`.

**COPY/MOVE** use engine `copy`/`move` and answer `COPYUID`; a missing destination answers
`NO [TRYCREATE]`.

**Errors.** Syntax errors answer `BAD`; engine errors answer `NO [code] ENGINE_CODE: message`
(`NOT_FOUND` -> `NONEXISTENT`, `PERMISSION_DENIED` -> `NOPERM`, `RUN_NOT_ACTIVE` ->
`UNAVAILABLE`, `SIMULATION_LIMIT_EXCEEDED`/`RATE_LIMITED` -> `LIMIT`, `INVALID_REQUEST`/
`UNSUPPORTED_OPERATION` -> `CANNOT`, `CONFLICT` -> `INUSE`).

**Framing limits.** Command line 64 KiB (`* BYE` beyond it); synchronizing literals up to
25 MiB + 64 KiB (larger ones answer `NO [TOOBIG]` without the `+` continuation; an oversized
`LITERAL+` literal closes the connection); autologout after 30 minutes idle. Plaintext pipelined
after `STARTTLS` closes the connection (command-injection guard).

## SMTP profile

aiosmtpd's `SMTP` protocol with a sandbox handler (the relaying `aiosmtpd.handlers` module is
banned). `EHLO` advertises `SIZE 26214400`, `8BITMIME`, `AUTH PLAIN LOGIN`, and `STARTTLS` when
TLS is configured.

| Step        | Behaviour                                                                     |
|-------------|-------------------------------------------------------------------------------|
| `AUTH`      | required before `MAIL` (`530 5.7.0` otherwise); PLAIN or LOGIN; `535 5.7.8` on failure |
| `MAIL FROM` | must be the authenticated mailbox (`553 5.7.1` otherwise); null sender refused |
| `RCPT TO`   | per recipient: simulated mailbox -> `250`; outside the simulation -> `550 5.7.1 ... EXTERNAL_DELIVERY_BLOCKED` when the run is in `deny` mode or the agent is prohibited from `external_delivery`, otherwise `250` (handled by the `sink`/`event` policy, never delivered); bad syntax `501 5.1.3`; more than 100 -> `452 4.5.3` |
| `DATA`      | parsed with `mailsim.mime` (25 MiB limit: `552`); `250 2.0.0 OK queued as <Message-ID> id=<message id>` |

Partial rejection works the usual way: rejected recipients get their own 5xx and the message is
committed for the accepted ones only (smtplib's `sendmail` returns them in its refused dict).

**What a submission becomes.** The envelope decides who receives the message: header `To`/`Cc`
addresses that are envelope recipients stay `To`/`Cc`, other envelope recipients become `Bcc`.

1. `In-Reply-To` naming a message in the mailbox, recipients exactly those of a reply or
   reply-all to it, and no HTML part -> engine `reply` (one `reply` action; threads, sets
   `\Answered` on the original; the subject is the engine's `Re: ...`).
2. Otherwise, with `In-Reply-To`/`References` -> engine `draft` + `send` of that draft (two
   actions; reply headers and threading preserved).
3. Otherwise -> engine `send` (one action).

Engine errors map to replies carrying the engine code: `EXTERNAL_DELIVERY_BLOCKED`/
`PERMISSION_DENIED` `550 5.7.1`, `SIMULATION_LIMIT_EXCEEDED` `554 5.3.4`, `RUN_NOT_ACTIVE`
`554 5.3.2`, `INVALID_REQUEST` `554 5.6.0`, `RATE_LIMITED` `451 4.7.1`, others `451 4.3.0`.

## Deviations (by design or pending engine support)

- **No implicit `\Seen`.** `BODY[]`/`RFC822` behave like `BODY.PEEK[]`: setting `\Seen` is an
  engine write that spends budget and, in scenarios that do not allow `flag`, would record a
  denied action the agent never asked for. Clients mark messages read with `STORE`.
- **Message-ID is assigned by the engine.** The client's `Message-ID`, `Date` and other
  headers are replaced; the assigned `Message-ID` is returned in the `250` DATA reply.
- **No attachments over SMTP** (`554 5.6.1 UNSUPPORTED_OPERATION`): the agent send operations
  carry text/HTML only. APPEND accepts any MIME.
- **Replies via SMTP** use engine `reply` only in the exact case above; other replies cost two
  actions (`draft` + `send`) and need both operations allowed by the scenario.
- **APPEND date-time** is accepted and ignored (internal date = virtual clock).
- **Folders are fixed**; no user folders, no hierarchy.
- **No IDLE / CONDSTORE**: poll with `NOOP`. Every IMAP command performs audited `read`s
  (folder list + listing), so polling shows up in efficiency metrics.
- **SEARCH** matches decoded text by substring (not RFC 3501's exact octet rules for every
  charset); `NEW`/`RECENT` never match because `\Recent` is never set.
- **`EMAILID` after COPY** is a new id (RFC 8474 says SHOULD keep it).
- STARTTLS is optional on both listeners (no `LOGINDISABLED`, AUTH allowed in clear) because
  they are meant for loopback / the internal sandbox network. Use `--tls` and a real
  certificate when clients connect across hosts.

## Engine access notes

The listeners use only `AgentMailbox`, plus three read-only lookups the facade does not offer:
the raw stored MIME (`engine.blobs.get(message.raw_blob_ref)`, falling back to a rebuild via
`mailsim.mime.build` without attachments), the bound mailbox's address (to match the login
name) and the run's delivery mode (for per-recipient RCPT decisions). None of these reach
scenario specs or answer keys.
