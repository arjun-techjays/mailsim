"""Assertion-type plugin registry (spec 14.7: explicit interfaces, no hard-coded branching).

WS-B implements one plugin per type ("state", "action", "content", "policy", "safety",
"invariant", "temporal", "protocol", "semantic").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from mailsim.evaluation.contract import AssertionOutcome
from mailsim.scenario.answer_key import AnswerAssertion


@dataclass
class EvaluationContext:
    """Everything a plugin may read. Built from the RECORDED run only (never live calls)."""

    run: dict[str, Any]  # run_info
    scenario: dict[str, Any]  # scenario spec the agent ran against (no answer key inside)
    final_state: dict[str, Any]  # snapshot at end of run
    events: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    agent_mailbox_id: str
    persona_mailboxes: dict[str, str]  # persona id -> mailbox id
    state_at: Callable[[int], dict[str, Any]]  # snapshot after a given event sequence
    judge: Any | None = None  # LLM judge (WS-G) or None
    extras: dict[str, Any] = field(default_factory=dict)


class AssertionEvaluator(Protocol):
    type: str

    def evaluate(self, ctx: EvaluationContext, assertion: AnswerAssertion) -> AssertionOutcome: ...


_REGISTRY: dict[str, AssertionEvaluator] = {}


def register(plugin: AssertionEvaluator) -> AssertionEvaluator:
    _REGISTRY[plugin.type] = plugin
    return plugin


def get(assertion_type: str) -> AssertionEvaluator | None:
    return _REGISTRY.get(assertion_type)


def registered() -> list[str]:
    return sorted(_REGISTRY)
