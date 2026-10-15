from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mailsim.llm.contract import LLMRequest, LLMResponse, ProviderName

STUB_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm_stub" / "responses.json"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live_llm: calls a real model; skipped without credentials")


class ScriptedProvider:
    """Counting fake provider: returns queued texts (or raises queued exceptions)."""

    name: ProviderName = "stub"

    def __init__(self, *outputs: Any, in_tokens: int = 10, out_tokens: int = 5) -> None:
        self.outputs = list(outputs)
        self.requests: list[LLMRequest] = []
        self.in_tokens, self.out_tokens = in_tokens, out_tokens

    @property
    def calls(self) -> int:
        return len(self.requests)

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        out = self.outputs.pop(0) if self.outputs else "ok"
        if isinstance(out, BaseException):
            raise out
        if isinstance(out, LLMResponse):
            return out
        return LLMResponse(text=out, provider="stub", model="fake-1",
                           input_tokens=self.in_tokens, output_tokens=self.out_tokens)  # fmt: skip


def fake_key(prefix: str = "sk-ant-") -> str:
    """Build fake secrets at runtime so secret scanners never see a literal key."""
    return prefix + "-".join(["api03", "FAKE" * 3, "0" * 8])
