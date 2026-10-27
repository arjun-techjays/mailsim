"""`action`: count matching entries in the recorded action log.

`operation: reply` counts a completed reply only when it answered someone, by the same rule
as ANSWERED (RunIndex.answers_nobody): its mail reached the replied message's From or
Reply-To, and that message is not one the agent made itself. Excluded replies are listed in
the diff so a FAIL says why.
"""

from __future__ import annotations

from typing import Any

from mailsim.evaluation.checks._common import (
    MAX_LISTED,
    OWN_MESSAGE,
    SENDER_NOT_REACHED,
    UNKNOWN_MESSAGE,
    action_matches,
    begin,
    count_dict,
    count_ok,
    decided,
    index,
    match_dict,
    outcome,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import ActionCheck, ActionMatch, AnswerAssertion

CAUSES = {
    SENDER_NOT_REACHED: "they did not reach the replied message's sender (From / Reply-To)",
    OWN_MESSAGE: "they answered mail the agent made itself (its own sent, inserted, drafted "
    "or self-delivered mail, or a copy of it)",
    UNKNOWN_MESSAGE: "the replied message is not in the recording",
}


class ActionPlugin:
    type = "action"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = ActionCheck.model_validate(assertion.check)
        match = ActionMatch.model_validate(check.model_dump(exclude={"count"}))
        idx = index(ctx)
        candidates = [o for o in idx.occurrences_in(point.cutoff)
                      if action_matches(match, o, idx=idx, answers_only=False)]  # fmt: skip
        causes = {o.ref: idx.unanswered(o) for o in candidates}
        hits = [o for o in candidates if causes[o.ref] is None]
        not_answers = {ref: why for ref, why in causes.items() if why is not None}
        matched = count_ok(check.count, len(hits))
        why = (f"{len(hits)} matching {match.actor} {match.operation!r} action(s) "
               f"(outcome {match.outcome}) up to {point.describe()}; "
               f"expected count {count_dict(check.count)}")  # fmt: skip
        for cause, text in CAUSES.items():
            n = sum(1 for c in not_answers.values() if c == cause)
            if n:
                why += f"; {n} reply action(s) not counted: {text}"
        actual: dict[str, Any] = {"count": len(hits), "actions": [o.ref for o in hits][:MAX_LISTED]}
        if not_answers:
            listed = list(not_answers)[:MAX_LISTED]
            actual["excluded_not_answering"] = listed
            actual["excluded_reasons"] = {ref: not_answers[ref] for ref in listed}
        diff = AssertionDiff(
            expected={**match_dict(match), "count": count_dict(check.count)},
            actual=actual,
            matched=matched,
            explanation=why,
        )
        evidence: list[EvidenceRef] = [e for o in hits[:MAX_LISTED] for e in o.evidence()]
        return outcome(assertion, decided(matched), why, diff=diff, evidence=evidence)


plugin = register(ActionPlugin())
