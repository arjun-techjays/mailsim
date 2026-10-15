"""Frozen LLM gateway contract (Wave 0). WS-G implements providers and the gateway.

Defaults (user-approved): Claude via the Anthropic SDK (`claude-sonnet-5-5`; `claude-opus-5-5`
selectable), Ollama through an OpenAI-compatible endpoint for local models, and a
deterministic offline `stub` for CI. Every generation used in a run is recorded as an event
payload so replay never calls a model (spec 6.3).
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "claude-sonnet-5-5"
ProviderName = Literal["anthropic", "openai_compatible", "stub"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(_M):
    role: Literal["user", "assistant"]
    content: str


class LLMRequest(_M):
    purpose: Literal["persona", "judge", "generation"]
    system: str = ""
    messages: list[Message]
    max_tokens: int = Field(default=1024, ge=1, le=32_000)
    temperature: float | None = Field(default=None, ge=0, le=1)
    run_id: str | None = None


class LLMResponse(_M):
    text: str
    provider: ProviderName
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None


class LLMProvider(Protocol):
    name: ProviderName

    def generate(self, request: LLMRequest) -> LLMResponse: ...


class Budget(_M):
    """Per-run limits enforced by the gateway (SIMULATION_LIMIT_EXCEEDED when exceeded)."""

    max_calls: int = Field(default=200, ge=0)
    max_input_tokens: int = Field(default=500_000, ge=0)
    max_output_tokens: int = Field(default=100_000, ge=0)
