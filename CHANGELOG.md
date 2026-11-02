# Changelog

Behaviour changes that can alter grading of existing recordings or break existing scenarios
and clients. Newest first. Re-grading notes explain how an older recording can grade
differently today.

## Grading, agent interfaces and scenario packs

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
  [the answer-key notes](docs/reference.md#answer-keys-evaluations-and-replays)); policy `forbid` is unchanged. **Re-grading:** stored evaluation
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
  `connection_test` (see the
  [operator guide](docs/operator-guide.md#agent-keys-endpoints-and-errors)). Answer-key operation and outcome names match ignoring case;
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

## Engine semantics (QA-A1)

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
