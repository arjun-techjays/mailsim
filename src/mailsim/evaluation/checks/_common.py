"""Shared machinery for assertion plugins: a read-only index over the RECORDED run.

Everything here is derived from `EvaluationContext` (event log, action log, snapshots); no
plugin ever calls the live engine.

Action <-> event correlation. The audit action log (`actions`) carries operation inputs and
outcomes but no sequence numbers; the event log carries sequences and virtual time. Agent
writes are anchored to their `agent.action_counted` event (exactly one per budget-counted
command, emitted after the command's own events). An agent command is counted iff it is a
write that passed the guard: guard rejections (permission, run state, budget, unknown
mailbox) emit no event. Other actors are anchored to the first following event with the same
actor and operation. Reads emit no events; they are placed after the last anchored event.
Action references in evidence are 1-based positions in the run's action log (`#n`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mailsim.domain.clock import parse_ts
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef, Status
from mailsim.evaluation.registry import EvaluationContext
from mailsim.evaluation.rules import pre_status
from mailsim.scenario.answer_key import ActionMatch, AnswerAssertion, Count, MessageWhere

AGENT = "agent"
READ_OPERATIONS = frozenset({"read", "search"})
# Agent rejections raised by the engine's guard, before anything is counted or emitted.
GUARD_CODES = frozenset({"PERMISSION_DENIED", "RUN_NOT_ACTIVE", "SIMULATION_LIMIT_EXCEEDED"})
AGENT_CREATED_EVENTS = frozenset({"message.inserted", "message.copied", "draft.created"})
MAX_LISTED = 25


@dataclass(frozen=True)
class Occurrence:
    """One recorded action, placed in the event log."""

    index: int  # 1-based position in the action log
    actor: str
    operation: str
    outcome: str
    input: dict[str, Any]
    result: dict[str, Any]
    first_seq: int | None  # first event emitted by the command (None: emitted nothing)
    last_seq: int | None  # anchor event
    before_seq: int  # last event sequence before the command started

    @property
    def seq(self) -> int:
        return self.last_seq if self.last_seq is not None else self.before_seq

    @property
    def ref(self) -> str:
        return f"#{self.index}"

    def evidence(self) -> list[EvidenceRef]:
        out = [EvidenceRef(kind="action", ref=self.ref,
                           note=f"{self.actor} {self.operation} -> {self.outcome}")]  # fmt: skip
        if self.last_seq is not None:
            out.append(EvidenceRef(kind="event", ref=str(self.last_seq)))
        return out


def _message_of(result: dict[str, Any]) -> str:
    msg = result.get("message")
    return msg if isinstance(msg, str) else ""


def counted(actor: str, operation: str, outcome: str, result: dict[str, Any]) -> bool:
    """Whether an agent action emitted an `agent.action_counted` event."""
    if actor != AGENT or operation in READ_OPERATIONS:
        return False
    if outcome in GUARD_CODES:
        return False
    return not (outcome == "NOT_FOUND" and _message_of(result).startswith("mailbox "))


class RunIndex:
    """Lazily built correlation of actions, events and created messages."""

    def __init__(self, events: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
        self.events = events
        self.by_seq = {e["sequence"]: e for e in events}
        self.last_seq = events[-1]["sequence"] if events else 0
        resets = [e["sequence"] for e in events if e["type"] == "run.reset"]
        self.epoch_start = resets[-1] if resets else 0
        self.occurrences = self._anchor(actions)
        self.agent_created = {
            e["payload"]["message"]["id"]
            for e in events
            if e["type"] in AGENT_CREATED_EVENTS and e["actor"] == AGENT
        }

    # ------------------------------------------------------------------ correlation
    def _anchor(self, actions: list[dict[str, Any]]) -> list[Occurrence]:
        evs = self.events
        pos = 0  # next unconsumed event position
        out: list[Occurrence] = []
        for i, a in enumerate(actions, 1):
            actor, op, outcome = a["actor"], a["operation"], a["outcome"]
            result = a.get("result") or {}
            last_known = evs[pos - 1]["sequence"] if pos else 0
            hit: int | None = None
            if actor == AGENT:
                if counted(actor, op, outcome, result):
                    hit = self._find(pos, type="agent.action_counted", actor=AGENT, operation=op,
                                     outcome=outcome)  # fmt: skip
            elif outcome == "ok":
                hit = self._find(pos, actor=actor, operation=op)
            if hit is None:
                out.append(Occurrence(i, actor, op, outcome, a.get("input") or {}, result,
                                      None, None, last_known))  # fmt: skip
                continue
            first = last = hit
            if actor == AGENT:  # walk back over the command's own events
                while first > 0 and self._same_command(evs[first - 1], op):
                    first -= 1
            else:  # extend over the contiguous events of the same command
                while (
                    last + 1 < len(evs)
                    and evs[last + 1]["actor"] == actor
                    and (evs[last + 1]["operation"] == op)
                ):
                    last += 1
            out.append(Occurrence(i, actor, op, outcome, a.get("input") or {}, result,
                                  evs[first]["sequence"], evs[last]["sequence"],
                                  evs[first]["sequence"] - 1))  # fmt: skip
            pos = last + 1
        return out

    def _find(self, start: int, **fields: str) -> int | None:
        for j in range(start, len(self.events)):
            ev = self.events[j]
            if all(ev[k] == v for k, v in fields.items()):
                return j
        return None

    @staticmethod
    def _same_command(ev: dict[str, Any], op: str) -> bool:
        return bool(
            ev["actor"] == AGENT and ev["operation"] == op and ev["type"] != "agent.action_counted"
        )

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
        idx = RunIndex(ctx.events, ctx.actions)
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


def precheck(
    ctx: EvaluationContext, assertion: AnswerAssertion, *, always_apply: bool = False
) -> AssertionOutcome | None:
    """rules.pre_status first. `always_apply` keeps safety invariants on with checks off."""
    checks_enabled = bool(ctx.extras.get("checks_enabled", True))
    pre = pre_status(assertion, checks_enabled=checks_enabled or always_apply,
                     judge_available=ctx.judge is not None)  # fmt: skip
    if pre is not None:
        return outcome(assertion, pre[0], pre[1])
    if not checks_enabled and not always_apply and assertion.type != "semantic":
        # rules.pre_status keeps every safety-CATEGORY assertion on; the product rule is that
        # only safety/invariant TYPES (sandbox guarantees) survive checks-off. A policy check
        # is a grading key: with checks off it is NOT_APPLICABLE, never FAIL.
        return outcome(assertion, Status.NOT_APPLICABLE, "deterministic checks are switched off")
    return None


def begin(
    ctx: EvaluationContext, assertion: AnswerAssertion, *, always_apply: bool = False
) -> AssertionOutcome | Point:
    """Pre-status, then the evaluation point. An outcome means: stop, this is the result."""
    pre = precheck(ctx, assertion, always_apply=always_apply)
    if pre is not None:
        return pre
    point = resolve_point(ctx, assertion)
    if isinstance(point, str):
        return outcome(assertion, Status.INCONCLUSIVE, point)
    return point


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


def action_matches(match: ActionMatch, occ: Occurrence) -> bool:
    return (
        occ.actor == match.actor
        and occ.operation == match.operation
        and (match.outcome == "any" or occ.outcome == match.outcome)
        and subset(match.input, occ.input)
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
) -> Selection:
    """Apply a MessageWhere to a state snapshot (all conditions AND-ed)."""
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
        labels = {_cf(x) for x in msg.get("labels") or []}
        flags = {str(x).upper() for x in msg.get("flags") or []}
        if where.label is not None and where.label.casefold() not in labels:
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
        picked.append(msg)
    return Selection(picked, excluded)


def where_dict(where: MessageWhere) -> dict[str, Any]:
    return where.model_dump(by_alias=True, exclude_defaults=True)
