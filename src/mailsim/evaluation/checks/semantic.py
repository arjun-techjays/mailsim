"""`semantic`: an LLM judge grades the agent's SENT messages against a rubric.

Only the requirement, the rubric and agent-sent texts reach the judge (never check data,
expected values or state). Without a judge the outcome is INCONCLUSIVE (rules.pre_status).
A judge error or an undecided judgement is INCONCLUSIVE as well; semantic results are
advisory by default and never override a hard failure.
"""

from __future__ import annotations

from typing import Any

from mailsim.evaluation.checks._common import (
    AGENT,
    MAX_LISTED,
    begin,
    index,
    outcome,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef, Status
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import AnswerAssertion


def _sent_texts(ctx: EvaluationContext, cutoff: int) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    ids: list[str] = []
    for e in index(ctx).events_in(cutoff):
        if e["type"] == "message.sent" and e["actor"] == AGENT:
            msg = e["payload"]["message"]
            ids.append(msg["id"])
            texts.append(f"Subject: {msg.get('subject') or ''}\n\n"
                         f"{msg.get('body_text') or msg.get('body_html') or ''}")  # fmt: skip
    return texts, ids


def _score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


class SemanticPlugin:
    type = "semantic"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        judge = ctx.judge
        assert judge is not None  # rules.pre_status made it INCONCLUSIVE otherwise
        texts, ids = _sent_texts(ctx, point.cutoff)
        expected = {"requirement": assertion.requirement, "rubric": assertion.rubric}
        msg_refs = [EvidenceRef(kind="message", ref=i, note="agent-sent") for i in ids]
        try:
            raw = judge.judge(requirement=assertion.requirement,
                                  rubric=assertion.rubric or assertion.requirement,
                                  agent_texts=texts)  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - a judge failure is never a verdict
            why = f"judge error: {type(exc).__name__}"
            return outcome(assertion, Status.INCONCLUSIVE, why,
                           diff=AssertionDiff(expected=expected, explanation=why),
                           evidence=msg_refs[:MAX_LISTED])  # fmt: skip
        result = raw if isinstance(raw, dict) else {}
        ctx.extras.setdefault("judge_calls", []).append(result)
        passed = result.get("passed")
        status = (Status.PASS if passed is True else
                  Status.FAIL if passed is False else Status.INCONCLUSIVE)  # fmt: skip
        why = str(result.get("explanation") or "")[:2000] or f"judge returned {status.value}"
        score = _score(result.get("score"))
        evidence = list(msg_refs)
        call_id = result.get("llm_call_id")
        if isinstance(call_id, str) and call_id:
            evidence.insert(0, EvidenceRef(kind="llm", ref=call_id, note="judge call"))
        diff = AssertionDiff(
            expected=expected,
            actual={
                "messages_judged": len(texts),
                "passed": passed if isinstance(passed, bool) else None,
                "score": score,
            },  # fmt: skip
            matched=passed if isinstance(passed, bool) else None,
            explanation=why,
        )
        return outcome(assertion, status, why, diff=diff, evidence=evidence[:MAX_LISTED],
                       score=score)  # fmt: skip


plugin = register(SemanticPlugin())
