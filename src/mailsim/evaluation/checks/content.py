"""`content`: text expectations over selected messages (default: the agent's Sent folder).

A message satisfies the check when it contains every `contains` string, none of the
`not_contains` strings and (if given) matches `regex`. `count` applies to the number of
satisfying messages, so "no sent message claims X" is `contains: [X], count: {eq: 0}`.
"""

from __future__ import annotations

import re
from typing import Any

from mailsim.evaluation.checks._common import (
    MAX_LISTED,
    begin,
    count_dict,
    count_ok,
    decided,
    index,
    mailbox_unknown,
    message_text,
    outcome,
    select_messages,
    state_ref,
    where_dict,
)
from mailsim.evaluation.contract import AssertionDiff, AssertionOutcome, EvidenceRef, Status
from mailsim.evaluation.registry import EvaluationContext, register
from mailsim.scenario.answer_key import AnswerAssertion, ContentCheck

MAX_TEXT = 64 * 1024  # per message: bounds regex work on agent-controlled text


def nested_quantifier(pattern: str) -> bool:
    """True when a quantified group itself contains an unbounded quantifier, e.g. `(a+)+`,
    `(\\w+\\s?)*` or `(x{2,})+`: the classic catastrophic-backtracking shape. Answer keys are
    trusted but agent text is not, so such patterns are refused rather than run."""
    stack: list[bool] = []  # per open group: does it contain a quantifier?
    inner = False  # quantifier seen in the current innermost group (or top level)
    i, in_class = 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            stack.append(inner)
            inner = False
        elif ch == ")" and stack:
            group_has = inner
            inner = stack.pop() or group_has
            nxt = pattern[i + 1 : i + 2]
            if group_has and nxt in ("*", "+", "{"):
                return True
        elif ch in "*+" or (ch == "{" and re.match(r"\{\d*,", pattern[i:])):
            inner = True
        i += 1
    return False


def _inspect(check: ContentCheck, msg: dict[str, Any]) -> dict[str, Any]:
    text = message_text(msg)[:MAX_TEXT]
    hay = text if check.case_sensitive else text.casefold()

    def norm(s: str) -> str:
        return s if check.case_sensitive else s.casefold()

    missing = [s for s in check.contains if norm(s) not in hay]
    present = [s for s in check.not_contains if norm(s) in hay]
    regex_ok: bool | None = None
    if check.regex is not None:
        flags = 0 if check.case_sensitive else re.IGNORECASE
        regex_ok = re.search(check.regex, text, flags) is not None
    ok = not missing and not present and regex_ok is not False
    return {"id": msg["id"], "satisfied": ok, "missing": missing, "forbidden_present": present,
            "regex_matched": regex_ok}  # fmt: skip


class ContentPlugin:
    type = "content"

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome:
        point = begin(ctx, assertion)
        if isinstance(point, AssertionOutcome):
            return point
        check = ContentCheck.model_validate(assertion.check)
        if check.regex is not None and nested_quantifier(check.regex):
            return outcome(assertion, Status.INCONCLUSIVE,
                           "regex refused: nested quantifiers risk catastrophic backtracking")  # fmt: skip
        missing = mailbox_unknown(ctx, assertion, check.where)
        if missing is not None:
            return missing
        sel = select_messages(point.state(ctx), check.where, agent_mailbox_id=ctx.agent_mailbox_id,
                              agent_created=index(ctx).agent_created)  # fmt: skip
        details = [_inspect(check, m) for m in sel.messages]
        good = [d for d in details if d["satisfied"]]
        matched = count_ok(check.count, len(good))
        why = (f"{len(good)} of {len(details)} candidate message(s) satisfy the content "
               f"expectation at {point.describe()}; expected count {count_dict(check.count)}")  # fmt: skip
        if sel.note:
            why += f" ({sel.note})"
        diff = AssertionDiff(
            expected={
                "where": where_dict(check.where),
                "contains": check.contains,
                "not_contains": check.not_contains,
                "regex": check.regex,
                "case_sensitive": check.case_sensitive,
                "count": count_dict(check.count),
            },
            actual={**sel.actual(), "satisfying": len(good), "messages": details[:MAX_LISTED]},
            matched=matched,
            explanation=why,
        )
        evidence = [state_ref(point)] + [
            EvidenceRef(kind="message", ref=d["id"],
                        note="satisfies" if d["satisfied"] else "does not satisfy")
            for d in details[:MAX_LISTED]
        ]  # fmt: skip
        return outcome(assertion, decided(matched), why, diff=diff, evidence=evidence)


plugin = register(ContentPlugin())
