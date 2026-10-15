"""`temporal`: virtual-time gap between two recorded happenings.

`first` is its first occurrence; `then` is the first occurrence strictly after it in the event
log. An event ref is a scenario event firing (`event.fired`), an action ref a matching action.
If `first` never happened the expectation is vacuous (NOT_APPLICABLE); if `then` never
followed it, or the gap breaks `within_seconds` / `min_seconds`, the assertion fails. A
matched happening without a recorded virtual time makes the gap unmeasurable: INCONCLUSIVE.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mailsim.domain.clock import parse_ts
from mailsim.evaluation.checks._common import (
    Point,
    action_matches,
    begin,
    decided,
    index,
    match_dict,
    outcome,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef, Status
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import AnswerAssertion, EventRef, TemporalCheck

Hit = tuple[tuple[int, int], datetime | None, list[EvidenceRef]]


def _hits(ctx: EvaluationContext, ref: EventRef, p: Point) -> list[Hit]:
    idx = index(ctx)
    out: list[Hit] = []
    if ref.event is not None:
        for e in idx.events_in(p.cutoff):
            if e["type"] == "event.fired" and e["payload"].get("id") == ref.event:
                seq = int(e["sequence"])
                vt = e.get("virtual_time")
                out.append(((seq, 0), parse_ts(vt) if vt else None,
                            [EvidenceRef(kind="event", ref=str(seq), note=f"fired {ref.event}")]))  # fmt: skip
    else:
        assert ref.action is not None  # enforced by EventRef
        for o in idx.occurrences_in(p.cutoff):
            if action_matches(ref.action, o):
                # an action that wrote no events sits just after event seq_start - 1
                out.append(((o.seq, 0 if o.wrote_events else 1), o.time, o.evidence()))
    return out


def _ref_dict(ref: EventRef) -> dict[str, Any]:
    return {"event": ref.event} if ref.event is not None else {"action": match_dict(ref.action)}  # type: ignore[arg-type]


class TemporalPlugin:
    type = "temporal"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = TemporalCheck.model_validate(assertion.check)
        expected: dict[str, Any] = {"first": _ref_dict(check.first), "then": _ref_dict(check.then),
                                    "within_seconds": check.within_seconds,
                                    "min_seconds": check.min_seconds}  # fmt: skip
        firsts = _hits(ctx, check.first, point)
        if not firsts:
            why = f"`first` never happened up to {point.describe()}; nothing to time"
            return outcome(assertion, Status.NOT_APPLICABLE, why,
                           diff=AssertionDiff(expected=expected, actual={"first": None},
                                              explanation=why))  # fmt: skip
        f_pos, f_time, f_ev = firsts[0]
        f_seq = f_pos[0]
        thens = [h for h in _hits(ctx, check.then, point) if h[0] > f_pos]
        if f_time is None or any(t is None for _, t, _ in thens[:1]):
            why = "a matched happening has no recorded virtual time; the gap cannot be measured"
            return outcome(assertion, Status.INCONCLUSIVE, why,
                           diff=AssertionDiff(expected=expected, explanation=why),
                           evidence=[*f_ev, *(r for h in thens[:1] for r in h[2])])  # fmt: skip
        actual: dict[str, Any] = {"first_sequence": f_seq, "first_time": f_time.isoformat()}
        if not thens:
            why = f"`then` never happened after `first` (sequence {f_seq})"
            return outcome(assertion, Status.FAIL, why,
                           diff=AssertionDiff(expected=expected, actual={**actual, "then": None},
                                              matched=False, explanation=why),
                           evidence=f_ev)  # fmt: skip
        (t_seq, _), t_time, t_ev = thens[0]
        assert t_time is not None  # checked above
        gap = (t_time - f_time).total_seconds()
        problems = []
        if check.within_seconds is not None and gap > check.within_seconds:
            problems.append(f"gap {gap:g}s exceeds within_seconds {check.within_seconds}")
        if check.min_seconds is not None and gap < check.min_seconds:
            problems.append(f"gap {gap:g}s is below min_seconds {check.min_seconds}")
        matched = not problems
        why = "; ".join(problems) or f"`then` followed `first` after {gap:g}s of virtual time"
        actual.update({"then_sequence": t_seq,
                       "then_time": t_time.isoformat(),
                       "gap_seconds": gap})  # fmt: skip
        return outcome(assertion, decided(matched), why,
                       diff=AssertionDiff(expected=expected, actual=actual, matched=matched,
                                          explanation=why),
                       evidence=[*f_ev, *t_ev])  # fmt: skip


plugin = register(TemporalPlugin())
