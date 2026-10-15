"""`action`: count matching entries in the recorded action log."""

from __future__ import annotations

from mailsim.evaluation.checks._common import (
    MAX_LISTED,
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


class ActionPlugin:
    type = "action"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = ActionCheck.model_validate(assertion.check)
        match = ActionMatch.model_validate(check.model_dump(exclude={"count"}))
        hits = [o for o in index(ctx).occurrences_in(point.cutoff) if action_matches(match, o)]
        matched = count_ok(check.count, len(hits))
        why = (f"{len(hits)} matching {match.actor} {match.operation!r} action(s) "
               f"(outcome {match.outcome}) up to {point.describe()}; "
               f"expected count {count_dict(check.count)}")  # fmt: skip
        diff = AssertionDiff(
            expected={**match_dict(match), "count": count_dict(check.count)},
            actual={"count": len(hits), "actions": [o.ref for o in hits][:MAX_LISTED]},
            matched=matched,
            explanation=why,
        )
        evidence: list[EvidenceRef] = [e for o in hits[:MAX_LISTED] for e in o.evidence()]
        return outcome(assertion, decided(matched), why, diff=diff, evidence=evidence)


plugin = register(ActionPlugin())
