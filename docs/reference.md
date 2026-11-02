# Grading and engine reference

How answer keys are checked and graded, how the action budget and per-run caps count, and the
engine's design and deliberate limits. Changes to these rules are listed in
[`CHANGELOG.md`](../CHANGELOG.md).

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
* `actor` is a trusted-caller argument of the engine. Agent interfaces reach the engine only
  through the `AgentMailbox` facade, which hard-codes it (enforced by import-linter).

## Architecture

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
  to prove none is opened. At the network level every simulator container (`app`, `api` with
  the IMAP/SMTP listeners, `egress-check`) sits only on the Compose `internal` network
  `sandbox`; the allowlist `egress-proxy` (HTTPS CONNECT to the LLM endpoint only) is the sole
  route out. The CI job `egress` proves from inside each container that SMTP 25/465/587 and
  arbitrary HTTP(S) fail; see [egress.md](egress.md).
* **Tenancy.** Application-scoped (`Scope(tenant, project)` on every repository/engine call;
  out-of-scope resources are `NOT_FOUND`). PostgreSQL row-level security is not used:
  isolation is enforced by the application and API keys.
