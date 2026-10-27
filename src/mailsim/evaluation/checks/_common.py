"""Shared machinery for assertion plugins: a read-only index over the RECORDED run.

Everything here is derived from `EvaluationContext` (event log, action log, snapshots); no
plugin ever calls the live engine.

Placement of actions. Every recorded action carries the exact event-log window its command
wrote (`seq_start..seq_end`, empty when `seq_end < seq_start`; an empty-window action sits
right after event `seq_start - 1`). The state "before" an action is therefore
`state_at(seq_start - 1)`, and an action belongs to a log prefix ending at `cutoff` iff its
position (`seq_end`, or `seq_start - 1` when empty) is `<= cutoff`. Evidence references
actions by their action-log id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses
from typing import Any

from mailsim.domain.clock import parse_ts
from mailsim.domain.models import is_system_label, normalize_address
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef, Status
from mailsim.evaluation.registry import EvaluationContext
from mailsim.evaluation.rules import pre_status
from mailsim.mime.parse import normalize_message_id
from mailsim.scenario.answer_key import ActionMatch, AnswerAssertion, Count, MessageWhere

AGENT = "agent"
AGENT_CREATED_EVENTS = frozenset({"message.inserted", "message.copied", "draft.created"})
MAX_LISTED = 25


def action_window(action: dict[str, Any]) -> tuple[int, int]:
    """(seq_start, seq_end) of an action; actions recorded without a window sit at 0."""
    start = int(action.get("seq_start") or 0)
    end = action.get("seq_end")
    return start, int(end) if end is not None else start - 1


def action_position(action: dict[str, Any]) -> int:
    """Last event sequence the action is placed after (its own last event if it wrote any)."""
    start, end = action_window(action)
    return end if end >= start else start - 1


@dataclass(frozen=True)
class Occurrence:
    """One recorded action, placed in the event log by its recorded window."""

    id: str
    actor: str
    operation: str
    outcome: str
    input: dict[str, Any]
    result: dict[str, Any]
    seq_start: int
    seq_end: int
    virtual_time: str | None

    @property
    def wrote_events(self) -> bool:
        return self.seq_end >= self.seq_start

    @property
    def before_seq(self) -> int:
        """Last event before the command started: its prior state is state_at(before_seq)."""
        return self.seq_start - 1

    @property
    def seq(self) -> int:
        return self.seq_end if self.wrote_events else self.before_seq

    @property
    def ref(self) -> str:
        return self.id

    @property
    def time(self) -> datetime | None:
        return parse_ts(self.virtual_time) if self.virtual_time else None

    def evidence(self) -> list[EvidenceRef]:
        out = [EvidenceRef(kind="action", ref=self.ref,
                           note=f"{self.actor} {self.operation} -> {self.outcome}")]  # fmt: skip
        if self.wrote_events:
            out.append(EvidenceRef(kind="event", ref=str(self.seq_end),
                                   note=f"events {self.seq_start}..{self.seq_end}"))  # fmt: skip
        return out


@dataclass(frozen=True)
class Answered:
    """What the agent actually answered (see RunIndex.answered_at): the recipients of its
    answers, per answered engine message id (replies) and per Message-ID header (sent mail
    whose In-Reply-To names it). Either counts only when it went to the message's sender."""

    recipients_by_id: dict[str, set[str]]
    recipients_by_header: dict[str, set[str]]

    def __contains__(self, msg: dict[str, Any]) -> bool:
        # a send to self or to someone else answers nothing, and a repeated Message-ID
        # (imported corpora) counts only the copy whose sender (From / Reply-To) was addressed
        senders = _senders(msg)
        by_id = self.recipients_by_id.get(msg["id"])
        if by_id is not None and not by_id.isdisjoint(senders):
            return True
        by_header = self.recipients_by_header.get(
            normalize_message_id(msg.get("message_id_header")) or ""
        )
        return by_header is not None and not by_header.isdisjoint(senders)


def _recipients(msg: Any) -> set[str]:
    if not isinstance(msg, dict):
        return set()
    return {normalize_address(a.get("email")) for k in ("to", "cc", "bcc")
            for a in msg.get(k) or [] if isinstance(a, dict)} - {""}  # fmt: skip


def _senders(msg: dict[str, Any]) -> set[str]:
    out = {normalize_address((msg.get("from") or {}).get("email"))}
    headers = msg.get("headers") or {}
    for name, values in headers.items():
        if str(name).casefold() == "reply-to":
            vals = values if isinstance(values, list) else [values]
            out |= {normalize_address(a) for _, a in getaddresses([str(v) for v in vals]) if a}
    return out - {""}


class RunIndex:
    """Placement of actions, events and agent-created messages within one recorded run."""

    def __init__(self, events: list[dict[str, Any]], actions: list[dict[str, Any]],
                 agent_mailbox_id: str = "") -> None:  # fmt: skip
        self.events = events
        self.by_seq = {e["sequence"]: e for e in events}
        self.last_seq = events[-1]["sequence"] if events else 0
        resets = [e["sequence"] for e in events if e["type"] == "run.reset"]
        self.epoch_start = resets[-1] if resets else 0
        self.occurrences = [self._occurrence(i, a) for i, a in enumerate(actions, 1)]
        self.agent_created: set[str] = set()
        # provenance of "sent": ids created by a real send (send/reply/forward/send_draft or a
        # persona's send). A message merely placed in Sent is not sent mail.
        self.sent_ids: set[str] = {
            e["payload"]["message"]["id"] for e in events
            if e["type"] == "message.sent" and isinstance(e["payload"].get("message"), dict)
        }  # fmt: skip
        # each message as it was created (From, headers incl. Reply-To), whatever happened to
        # it later: whom a reply had to reach is fixed when the replied message arrived
        self.origins: dict[str, dict[str, Any]] = {}
        self.agent_sent: set[str] = set()  # the agent's own sent mail (its Sent copies)
        for e in events:
            msg = e["payload"].get("message")
            if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                self.origins.setdefault(msg["id"], msg)
                if e["type"] == "message.sent" and e["actor"] == AGENT:
                    self.agent_sent.add(msg["id"])
        for e in events:
            if e["actor"] != AGENT or "message" not in e["payload"]:
                continue
            msg = e["payload"]["message"]
            # forged evidence: inserted / copied / drafted messages, and anything the agent
            # delivered into its own mailbox (send-to-self)
            if e["type"] in AGENT_CREATED_EVENTS or (
                e["type"] == "message.delivered" and msg.get("mailbox_id") == agent_mailbox_id
            ):
                self.agent_created.add(msg["id"])

    @staticmethod
    def _occurrence(i: int, a: dict[str, Any]) -> Occurrence:
        start, end = action_window(a)
        return Occurrence(str(a.get("id", i)), a["actor"], a["operation"], a["outcome"],
                          a.get("input") or {}, a.get("result") or {}, start, end,
                          a.get("virtual_time"))  # fmt: skip

    def agent_labels_at(self, cutoff: int) -> set[tuple[str, str]]:
        """(message id, casefolded label) pairs whose most recent ADD up to `cutoff` was made by
        the agent itself. Such labels are self-issued evidence: like agent-created mail they
        cannot satisfy a policy's `unless` (a label added by anyone else later wins)."""
        last_adder: dict[tuple[str, str], str] = {}
        for e in self.events_in(cutoff):
            if e["type"] != "message.labels_changed":
                continue
            mid = e["payload"].get("message_id", "")
            for label in e["payload"].get("add") or []:
                last_adder[(mid, str(label).casefold())] = e["actor"]
        return {k for k, actor in last_adder.items() if actor == AGENT}

    def answered_at(self, cutoff: int) -> Answered:
        """Provenance of ANSWERED up to `cutoff`: the recipients of the agent's real replies
        per replied message id (a recorded ok `reply`, incl. a draft sent as one; a forward is
        not an answer, it is $Forwarded), and of its sent mail per Message-ID its In-Reply-To
        names. A flag or label set by hand is not."""
        by_id: dict[str, set[str]] = {}
        for o in self.occurrences_in(cutoff):
            if (o.actor == AGENT and o.outcome == "ok" and o.operation == "reply"
                    and o.input.get("message_id") and o.wrote_events):  # fmt: skip
                by_id.setdefault(str(o.input["message_id"]), set()).update(self.reply_recipients(o))
        by_header: dict[str, set[str]] = {}
        for e in self.events_in(cutoff):
            msg = e["payload"].get("message")
            header = normalize_message_id(msg.get("in_reply_to")) if isinstance(msg, dict) else None
            if e["type"] == "message.sent" and e["actor"] == AGENT and header:
                by_header.setdefault(header, set()).update(_recipients(msg))
        return Answered(by_id, by_header)

    def reply_recipients(self, occ: Occurrence) -> set[str]:
        """Normalised To/Cc/Bcc of the mail an action sent (its own `message.sent` events)."""
        out: set[str] = set()
        for q in range(occ.seq_start, occ.seq_end + 1):
            e = self.by_seq.get(q)
            if e is not None and e["type"] == "message.sent":
                out |= _recipients(e["payload"].get("message"))
        return out

    def answers_nobody(self, occ: Occurrence) -> bool:
        """A completed `reply` that answered no one, which an answer key's `operation: reply`
        does not count. Same rule as ANSWERED: the reply's mail must reach the replied
        message's sender (its From or Reply-To). And that message must not be one the agent
        made itself: its own sent mail, or mail it inserted, copied, drafted or delivered to
        itself (a mail to self, then a "reply" to either copy, answers no one). Refused attempts
        and other operations are judged as recorded (False)."""
        if occ.operation != "reply" or occ.outcome != "ok":
            return False
        orig = self.origins.get(str(occ.input.get("message_id") or ""))
        if orig is None or orig["id"] in self.agent_created or orig["id"] in self.agent_sent:
            return True
        return self.reply_recipients(occ).isdisjoint(_senders(orig))

    # ------------------------------------------------------------------ windows
    def events_in(self, cutoff: int) -> list[dict[str, Any]]:
        """Current-epoch events up to and including `cutoff`."""
        return [e for e in self.events if self.epoch_start <= e["sequence"] <= cutoff]

    def occurrences_in(self, cutoff: int) -> list[Occurrence]:
        return [o for o in self.occurrences if self.epoch_start <= o.seq <= cutoff]

    def fired_seq(self, event_id: str) -> int | None:
        for e in self.events_in(self.last_seq):
            if e["type"] == "event.fired" and e["payload"].get("id") == event_id:
                return int(e["sequence"])
        return None

    def time_at(self, seq: int) -> datetime | None:
        """Virtual time right after event `seq` (latest stamped event at or before it)."""
        for s in range(seq, 0, -1):
            ev = self.by_seq.get(s)
            if ev is not None and ev.get("virtual_time"):
                return parse_ts(ev["virtual_time"])
        return None


def index(ctx: EvaluationContext) -> RunIndex:
    idx = ctx.extras.get("_index")
    if not isinstance(idx, RunIndex):
        idx = RunIndex(ctx.events, ctx.actions, ctx.agent_mailbox_id)
        ctx.extras["_index"] = idx
    return idx


def state_at(ctx: EvaluationContext, seq: int) -> dict[str, Any]:
    cache: dict[int, dict[str, Any]] = ctx.extras.setdefault("_states", {})
    if seq not in cache:
        cache[seq] = ctx.state_at(seq)
    return cache[seq]


# ---------------------------------------------------------------------- evaluation point
@dataclass(frozen=True)
class Point:
    cutoff: int  # last event sequence considered
    at_end: bool

    def state(self, ctx: EvaluationContext) -> dict[str, Any]:
        return ctx.final_state if self.at_end else state_at(ctx, self.cutoff)

    def describe(self) -> str:
        return "end of run" if self.at_end else f"after event sequence {self.cutoff}"


def resolve_point(ctx: EvaluationContext, assertion: AnswerAssertion) -> Point | str:
    """The assertion's evaluation point, or a reason string when it was never reached."""
    idx = index(ctx)
    ep = assertion.evaluate_at
    if ep.at == "end":
        return Point(idx.last_seq, True)
    assert ep.event is not None  # enforced by EvaluatePoint
    seq = idx.fired_seq(ep.event)
    if seq is None:
        return f"evaluation point event {ep.event!r} never fired in this run"
    return Point(seq, False)


# ---------------------------------------------------------------------- outcomes
def outcome(
    assertion: AnswerAssertion,
    status: Status,
    reason: str = "",
    *,
    diff: AssertionDiff | None = None,
    evidence: Iterable[EvidenceRef] = (),
    score: float | None = None,
) -> AssertionOutcome:
    return AssertionOutcome(
        assertion_id=assertion.id,
        type=assertion.type,
        category=assertion.effective_category,
        severity="advisory" if assertion.effective_severity == "advisory" else "mandatory",
        subject=assertion.subject,
        status=status,
        reason=reason,
        diff=diff or AssertionDiff(explanation=reason),
        evidence=list(evidence),
        score=score,
    )


def precheck(ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome | None:
    """rules.pre_status (checks switch by TYPE: safety/invariant always apply; judge)."""
    pre = pre_status(assertion, checks_enabled=bool(ctx.extras.get("checks_enabled", True)),
                     judge_available=ctx.judge is not None)  # fmt: skip
    return None if pre is None else outcome(assertion, pre[0], pre[1])


def begin(ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome | Point:
    """Pre-status, then the evaluation point. An outcome means: stop, this is the result."""
    pre = precheck(ctx, assertion)
    if pre is not None:
        return pre
    point = resolve_point(ctx, assertion)
    if isinstance(point, str):
        return outcome(assertion, Status.INCONCLUSIVE, point)
    return point


def mailbox_unknown(
    ctx: EvaluationContext, assertion: AnswerAssertion, *wheres: MessageWhere
) -> AssertionOutcome | None:
    """INCONCLUSIVE when a selection defaults to the agent's mailbox but none is known."""
    if not ctx.agent_mailbox_id and any(w.mailbox is None for w in wheres):
        return outcome(assertion, Status.INCONCLUSIVE,
                       "the agent's mailbox is unknown for this run")  # fmt: skip
    return None


def state_ref(point: Point) -> EvidenceRef:
    return EvidenceRef(kind="state", ref=f"state@{point.cutoff}", note=point.describe())


def decided(matched: bool) -> Status:
    return Status.PASS if matched else Status.FAIL


# ---------------------------------------------------------------------- counts
def count_ok(count: Count, n: int) -> bool:
    if count.eq is not None:
        return n == count.eq
    if count.min is not None and n < count.min:
        return False
    return not (count.max is not None and n > count.max)


def count_dict(count: Count) -> dict[str, int]:
    return count.model_dump(exclude_none=True)


# ---------------------------------------------------------------------- actions
def subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    return bool(expected == actual)


def action_matches(
    match: ActionMatch, occ: Occurrence, *, idx: RunIndex, answers_only: bool = True
) -> bool:
    """`match` against one recorded action. With `answers_only` (action and temporal checks)
    a completed `reply` counts only when it answered someone (RunIndex.answers_nobody); a
    policy `forbid` passes False, so it sees every reply operation the agent ran."""
    return (
        occ.actor == match.actor
        and occ.operation == match.operation
        and (match.outcome == "any" or occ.outcome == match.outcome)
        and subset(match.input, occ.input)
        and not (answers_only and idx.answers_nobody(occ))
    )


def match_dict(match: ActionMatch) -> dict[str, Any]:
    return match.model_dump(include={"actor", "operation", "outcome", "input"})


# ---------------------------------------------------------------------- messages
def _cf(value: str | None) -> str:
    return (value or "").casefold()


def _addr_matches(needle: str, addr: dict[str, Any]) -> bool:
    n = needle.casefold()
    email = _cf(addr.get("email"))
    if "@" in n:
        return email == n
    return n in email or n in _cf(addr.get("name"))


def message_text(msg: dict[str, Any]) -> str:
    return "\n".join(x for x in (msg.get("subject"), msg.get("body_text"),
                                 msg.get("body_html")) if x)  # fmt: skip


@dataclass
class Selection:
    messages: list[dict[str, Any]]
    excluded_agent_created: list[str]
    note: str = ""

    def actual(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "count": len(self.messages),
            "message_ids": [m["id"] for m in self.messages][:MAX_LISTED],
        }
        if self.excluded_agent_created:
            out["excluded_agent_created"] = self.excluded_agent_created[:MAX_LISTED]
        if self.note:
            out["note"] = self.note
        return out

    def evidence(self) -> list[EvidenceRef]:
        return [EvidenceRef(kind="message", ref=m["id"]) for m in self.messages][:MAX_LISTED]


def select_messages(
    state: dict[str, Any],
    where: MessageWhere,
    *,
    agent_mailbox_id: str,
    agent_created: set[str],
    agent_labels: set[tuple[str, str]] | None = None,
    sent_ids: set[str] | None = None,
    answered: Answered | None = None,
) -> Selection:
    """Apply a MessageWhere to a state snapshot (all conditions AND-ed).

    With `sent_ids` (RunIndex.sent_ids), `folder: sent` selects by PROVENANCE, not placement:
    only messages created by an actual send count, and `folder: drafts` only real drafts.
    Anything else found there was put there (forged evidence) and is excluded like
    agent-created mail, unless the key sets `include_agent_created`."""
    if where.mailbox is None:
        mailbox_id: str | None = agent_mailbox_id
    else:
        mailbox_id = next((mb["id"] for mb in state.get("mailboxes", [])
                           if mb.get("owner_id") == where.mailbox), None)  # fmt: skip
    if mailbox_id is None:
        return Selection([], [], f"no mailbox for persona {where.mailbox!r}")
    folders = {f["id"]: f for f in state.get("folders", []) if f.get("mailbox_id") == mailbox_id}
    with_attachment = {a["message_id"] for a in state.get("attachments", [])}
    headers = {m["message_id_header"]: m.get("subject") or "" for m in state.get("messages", [])}
    picked: list[dict[str, Any]] = []
    excluded: list[str] = []
    for msg in state.get("messages", []):
        if msg.get("mailbox_id") != mailbox_id:
            continue
        if where.folder is not None:
            folder = folders.get(msg.get("folder_id", ""))
            if folder is None or _cf(folder.get("system_type")) != where.folder.casefold():
                continue
        flags = {str(x).upper() for x in msg.get("flags") or []} - {"DRAFT"}
        if msg.get("is_draft"):
            flags.add("DRAFT")  # provenance: only a real draft is DRAFT, whatever was stored
        if answered is not None and msg.get("mailbox_id") == agent_mailbox_id:
            # in the agent's mailbox ANSWERED = it really replied (the agent can
            # set the flag itself); elsewhere the flag is the simulation's own
            flags.discard("ANSWERED")
            if msg in answered:
                flags.add("ANSWERED")
        if where.label is not None and not _has_label(
            msg, where.label, flags, folders.get(msg.get("folder_id", "")), sent_ids
        ):
            continue
        if where.has_flag is not None and where.has_flag.upper() not in flags:
            continue
        if where.lacks_flag is not None and where.lacks_flag.upper() in flags:
            continue
        if where.from_ is not None and not _addr_matches(where.from_, msg.get("from") or {}):
            continue
        rcpts = [*(msg.get("to") or []), *(msg.get("cc") or []), *(msg.get("bcc") or [])]
        if where.to is not None and not any(_addr_matches(where.to, r) for r in rcpts):
            continue
        if where.subject_contains is not None and (
            where.subject_contains.casefold() not in _cf(msg.get("subject"))
        ):
            continue
        body = _cf(msg.get("body_text")) + "\n" + _cf(msg.get("body_html"))
        if where.body_contains is not None and where.body_contains.casefold() not in body:
            continue
        if where.has_attachment is not None and (msg["id"] in with_attachment) != (
            where.has_attachment
        ):
            continue
        if where.in_reply_to_subject is not None:
            parent = headers.get(msg.get("in_reply_to") or "")
            if parent is None or where.in_reply_to_subject.casefold() not in parent.casefold():
                continue
        if not where.include_agent_created and msg["id"] in agent_created:
            excluded.append(msg["id"])
            continue
        if not where.include_agent_created and sent_ids is not None and (
            (where.folder is not None and where.folder.casefold() == "sent"
             and msg["id"] not in sent_ids)
            or (where.folder is not None and where.folder.casefold() == "drafts"
                and not msg.get("is_draft"))
        ):  # fmt: skip
            excluded.append(msg["id"])  # placed in Sent/Drafts, never sent/drafted
            continue
        if (
            where.label is not None
            and not where.include_agent_created
            and agent_labels is not None
            and (msg["id"], where.label.casefold()) in agent_labels
        ):
            excluded.append(msg["id"])  # the matching label was applied by the agent itself
            continue
        picked.append(msg)
    return Selection(picked, excluded)


def _has_label(msg: dict[str, Any], label: str, flags: set[str], folder: dict[str, Any] | None,
               sent_ids: set[str] | None) -> bool:  # fmt: skip
    """`label` on `msg`. A system label (SENT, DRAFT, INBOX, UNREAD, STARRED, ...) is derived
    from provenance, folder and flags, never read from the stored labels (a stored one was put
    there by hand): SENT = created by a real send (with `sent_ids`; else in Sent and not a
    draft), DRAFT/DRAFTS = a real draft. Other labels are the stored ones, ignoring case."""
    name = label.strip().casefold()
    if not is_system_label(name):
        return name in {_cf(x) for x in msg.get("labels") or []}
    folder_type = _cf((folder or {}).get("system_type"))
    if name == "sent":
        if sent_ids is not None:
            return msg["id"] in sent_ids
        return folder_type == "sent" and not msg.get("is_draft")
    if name in ("draft", "drafts"):
        return bool(msg.get("is_draft"))
    if name == "unread":
        return "SEEN" not in flags and not msg.get("is_draft")
    by_flag = {"starred": "FLAGGED", "answered": "ANSWERED", "deleted": "DELETED"}
    if name in by_flag:
        return by_flag[name] in flags
    return folder_type == name  # inbox, trash, archive


def where_dict(where: MessageWhere) -> dict[str, Any]:
    return where.model_dump(by_alias=True, exclude_defaults=True)
