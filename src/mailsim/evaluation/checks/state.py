"""`state`: deterministic query over the mailbox state at the evaluation point."""

from __future__ import annotations

from mailsim.evaluation.checks._common import (
    begin,
    count_dict,
    count_ok,
    decided,
    index,
    mailbox_unknown,
    outcome,
    select_messages,
    state_ref,
    where_dict,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import AnswerAssertion, StateCheck


class StatePlugin:
    type = "state"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = StateCheck.model_validate(assertion.check)
        missing = mailbox_unknown(ctx, assertion, check.where)
        if missing is not None:
            return missing
        idx = index(ctx)
        sel = select_messages(point.state(ctx), check.where, agent_mailbox_id=ctx.agent_mailbox_id,
                              agent_created=idx.agent_created, sent_ids=idx.sent_ids,
                              answered=idx.answered_at(point.cutoff))  # fmt: skip
        matched = count_ok(check.count, len(sel.messages))
        why = (f"{len(sel.messages)} message(s) match at {point.describe()}; "
               f"expected count {count_dict(check.count)}")  # fmt: skip
        if sel.excluded_agent_created:
            why += f" ({len(sel.excluded_agent_created)} agent-created message(s) ignored)"
        diff = AssertionDiff(
            expected={"where": where_dict(check.where), "count": count_dict(check.count)},
            actual=sel.actual(),
            matched=matched,
            explanation=why,
        )
        return outcome(assertion, decided(matched), why, diff=diff,
                       evidence=[state_ref(point), *sel.evidence()])  # fmt: skip


plugin = register(StatePlugin())
