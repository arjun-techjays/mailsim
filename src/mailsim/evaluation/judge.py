"""Semantic judge interface (structural; WS-G implements it in `mailsim.llm` without
importing this package).

The judge only ever sees the assertion's requirement + rubric and the texts the agent SENT.
It never receives deterministic check data, expected values, state or the event log, so it
cannot grade "its own unverified assumptions about the intended result" (spec 9.4).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Judge(Protocol):
    def judge(self, *, requirement: str, rubric: str, agent_texts: list[str]) -> dict[str, Any]:
        """Return {"passed": bool | None, "score": float | None, "explanation": str,
        "llm_call_id": str | None}. Optional extra keys used for metrics only:
        "model", "input_tokens", "output_tokens"."""
        ...


__all__ = ["Judge"]
