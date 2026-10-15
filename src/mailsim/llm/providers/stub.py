"""Deterministic offline provider for CI and local runs (no network, no randomness).

Responses are looked up by the sha256 of the canonical prompt (purpose + system + messages,
see ``mailsim.llm.gateway.prompt_hash``) in a JSON fixture table::

    {"version": 1, "responses": {"<sha256>": {"text": "...", "stop_reason": "end_turn"}}}

Unknown prompts get a deterministic fallback: a short persona reply, or an INCONCLUSIVE
judge verdict (``passed: null``) so CI never fabricates a semantic pass. Token counts are
deterministic (ceil(chars / 4), minimum 1).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from mailsim.domain.errors import InvalidRequest
from mailsim.llm.contract import LLMRequest, LLMResponse, ProviderName


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


class StubProvider:
    name: ProviderName = "stub"

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None, *,
                 model: str = "stub-1") -> None:  # fmt: skip
        self.responses = dict(responses or {})
        self.model = model

    @classmethod
    def from_path(cls, path: str | Path | None, *, model: str = "stub-1") -> StubProvider:
        if path is None:
            return cls(model=model)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InvalidRequest(f"cannot load stub fixtures: {type(exc).__name__}") from None
        responses = data.get("responses") if isinstance(data, dict) else None
        if not isinstance(responses, dict) or not all(
            isinstance(v, dict) and isinstance(v.get("text"), str) for v in responses.values()
        ):
            raise InvalidRequest("stub fixtures must be {'responses': {hash: {'text': str}}}")
        return cls(responses, model=model)

    def generate(self, request: LLMRequest) -> LLMResponse:
        from mailsim.llm.gateway import canonical_prompt, prompt_hash

        key = prompt_hash(request)
        entry = self.responses.get(key)
        if entry is not None:
            text, stop = entry["text"], str(entry.get("stop_reason", "end_turn"))
        else:
            text, stop = self._fallback(request, key), "end_turn"
        return LLMResponse(
            text=text,
            provider="stub",
            model=self.model,
            input_tokens=_tokens(canonical_prompt(request)),
            output_tokens=_tokens(text),
            stop_reason=stop,
        )

    @staticmethod
    def _fallback(request: LLMRequest, key: str) -> str:
        if request.purpose == "judge":
            return json.dumps({"passed": None, "score": None,
                               "explanation": f"stub judge: no fixture for {key[:12]}"})  # fmt: skip
        return (
            f"Thank you for your email. I will look into this and get back to you. [stub:{key[:8]}]"
        )
