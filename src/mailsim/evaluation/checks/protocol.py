"""`protocol`: count agent actions that ended in an integration error code."""

from __future__ import annotations

from mailsim.evaluation.checks._common import (
    AGENT,
    MAX_LISTED,
    begin,
    decided,
    index,
    outcome,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import AnswerAssertion, ProtocolCheck


class ProtocolPlugin:
    type = "protocol"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = ProtocolCheck.model_validate(assertion.check)
        codes = set(check.error_codes)
        errors = [o for o in index(ctx).occurrences_in(point.cutoff)
                  if o.actor == AGENT and o.outcome in codes]  # fmt: skip
        matched = len(errors) <= check.max_errors
        why = (f"{len(errors)} agent action(s) failed with {sorted(codes)} up to "
               f"{point.describe()}; at most {check.max_errors} allowed")  # fmt: skip
        by_code: dict[str, int] = {}
        for o in errors:
            by_code[o.outcome] = by_code.get(o.outcome, 0) + 1
        diff = AssertionDiff(
            expected={"max_errors": check.max_errors, "error_codes": check.error_codes},
            actual={
                "errors": len(errors),
                "by_code": by_code,
                "actions": [
                    {"action": o.ref, "operation": o.operation, "outcome": o.outcome}
                    for o in errors
                ][:MAX_LISTED],
            },  # fmt: skip
            matched=matched,
            explanation=why,
        )
        evidence = [r for o in errors[:MAX_LISTED] for r in o.evidence()]
        return outcome(assertion, decided(matched), why, diff=diff, evidence=evidence)


plugin = register(ProtocolPlugin())
