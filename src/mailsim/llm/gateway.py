"""Provider-neutral LLM gateway (WS-G).

The gateway is the only module that touches concrete providers. It

- selects a provider from configuration (``MAILSIM_LLM_PROVIDER``; offline ``stub`` default),
- enforces the per-run :class:`Budget` (``SIMULATION_LIMIT_EXCEEDED`` before a call that
  would exceed it; ``max_tokens`` is capped to the remaining output budget),
- records every successful call as an ``llm_calls`` row (prompt hash, tokens, latency),
- maps every provider failure to a typed :class:`SimError` with secrets redacted.

Model OUTPUTS are not stored here: callers record them in run events (persona replies go
through ``Controller.persona_reply``), so replay never calls a model (spec 6.3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailsim.domain.errors import InvalidRequest, RateLimited, SimError, SimulationLimitExceeded
from mailsim.domain.ids import new_control_id
from mailsim.llm.config import LLMConfig, redact
from mailsim.llm.contract import Budget, LLMProvider, LLMRequest, LLMResponse
from mailsim.store import models as m

log = logging.getLogger("mailsim.llm")
SessionFactory = Callable[[], Session]


class LLMUnavailable(RateLimited):
    """Transient provider failure (timeout, connection, 429/overloaded, 5xx). Retry later."""


class LLMProviderError(InvalidRequest):
    """Non-retryable provider failure (auth, rejected request, refusal, malformed output)."""


def provider_error(provider: str, kind: str, message: str, *, retryable: bool) -> SimError:
    """Build the typed error for a provider failure; the message is always redacted."""
    cls = LLMUnavailable if retryable else LLMProviderError
    return cls(
        redact(f"{provider} provider failed ({kind}): {message}")[:500],
        details={"provider": provider, "kind": kind, "retryable": retryable},
    )


def canonical_prompt(request: LLMRequest) -> str:
    """Canonical JSON of the prompt (purpose, system, messages): the hash/stub key input."""
    body = {
        "purpose": request.purpose,
        "system": request.system,
        "messages": [{"role": x.role, "content": x.content} for x in request.messages],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(request: LLMRequest) -> str:
    return hashlib.sha256(canonical_prompt(request).encode()).hexdigest()


@dataclass(frozen=True)
class GatewayResult:
    response: LLMResponse
    llm_call_id: str
    prompt_hash: str
    latency_ms: int


@dataclass
class _Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def build_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate the configured provider (imports are local: only the gateway may)."""
    if config.provider == "anthropic":
        from mailsim.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            effort=config.effort,
            fallbacks=config.fallbacks,
        )
    if config.provider == "openai_compatible":
        from mailsim.llm.providers.openai_compatible import OpenAICompatibleProvider

        assert config.base_url is not None  # enforced by LLMConfig.from_env
        return OpenAICompatibleProvider(
            base_url=config.base_url, model=config.model, timeout_seconds=config.timeout_seconds
        )
    from mailsim.llm.providers.stub import StubProvider

    return StubProvider.from_path(config.stub_fixtures, model=config.model)


class LLMGateway:
    """Budgeted, recorded access to one provider."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        session_factory: SessionFactory | None = None,
        budget: Budget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.budget = budget or Budget()
        self._sf = session_factory
        self._clock = clock
        self._mem: dict[str, _Usage] = {}  # used only without a session factory

    @classmethod
    def from_config(
        cls, config: LLMConfig | None = None, *, session_factory: SessionFactory | None = None
    ) -> LLMGateway:
        cfg = config or LLMConfig.from_env()
        return cls(build_provider(cfg), session_factory=session_factory, budget=cfg.budget)

    # ------------------------------------------------------------------ budget

    def usage(self, run_id: str) -> dict[str, int]:
        u = self._usage(run_id)
        return {"calls": u.calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens}

    def _usage(self, run_id: str) -> _Usage:
        if self._sf is None:
            return self._mem.get(run_id, _Usage())
        with self._sf() as s:
            row = s.execute(
                select(
                    func.count(m.LlmCall.id),
                    func.coalesce(func.sum(m.LlmCall.input_tokens), 0),
                    func.coalesce(func.sum(m.LlmCall.output_tokens), 0),
                ).where(m.LlmCall.run_id == run_id)
            ).one()
            return _Usage(int(row[0]), int(row[1]), int(row[2]))

    def _check_budget(self, request: LLMRequest) -> LLMRequest:
        if request.run_id is None:
            return request
        u, b = self._usage(request.run_id), self.budget
        exceeded = [
            name
            for name, used, cap in (
                ("max_calls", u.calls, b.max_calls),
                ("max_input_tokens", u.input_tokens, b.max_input_tokens),
                ("max_output_tokens", u.output_tokens, b.max_output_tokens),
            )
            if used >= cap
        ]
        if exceeded:
            raise SimulationLimitExceeded(
                f"LLM budget exhausted for run: {', '.join(exceeded)}",
                details={"limits": exceeded, "usage": self.usage(request.run_id)},
            )
        remaining = b.max_output_tokens - u.output_tokens
        if request.max_tokens > remaining:
            return request.model_copy(update={"max_tokens": remaining})
        return request

    # ------------------------------------------------------------------ calls

    def call(self, request: LLMRequest, *, evaluation_id: str | None = None) -> GatewayResult:
        request = self._check_budget(request)
        phash = prompt_hash(request)
        provider_name = str(getattr(self.provider, "name", "unknown"))
        started = self._clock()
        try:
            response = self.provider.generate(request)
        except SimError as exc:
            exc.message = redact(exc.message)
            log.warning("llm call failed provider=%s purpose=%s run=%s code=%s",
                        provider_name, request.purpose, request.run_id, exc.code)  # fmt: skip
            raise
        except Exception as exc:  # any SDK/transport surprise becomes a typed error
            log.warning("llm call failed provider=%s purpose=%s run=%s error=%s",
                        provider_name, request.purpose, request.run_id, type(exc).__name__)  # fmt: skip
            raise provider_error(provider_name, "unexpected", type(exc).__name__,
                                 retryable=False) from None  # fmt: skip
        latency_ms = max(0, int((self._clock() - started) * 1000))
        call_id = new_control_id("llm")
        self._record(call_id, request, response, phash, latency_ms, evaluation_id)
        log.info(
            "llm call provider=%s model=%s purpose=%s run=%s prompt_hash=%s in=%d out=%d ms=%d",
            response.provider, response.model, request.purpose, request.run_id, phash[:16],
            response.input_tokens, response.output_tokens, latency_ms,
        )  # fmt: skip
        return GatewayResult(response, call_id, phash, latency_ms)

    def generate(self, request: LLMRequest) -> LLMResponse:
        """LLMProvider-shaped convenience: budgeted + recorded, returns the response only."""
        return self.call(request).response

    def _record(self, call_id: str, request: LLMRequest, response: LLMResponse, phash: str,
                latency_ms: int, evaluation_id: str | None) -> None:  # fmt: skip
        if self._sf is None:
            if request.run_id is not None:
                u = self._mem.setdefault(request.run_id, _Usage())
                u.calls += 1
                u.input_tokens += response.input_tokens
                u.output_tokens += response.output_tokens
            return
        row: dict[str, Any] = {
            "id": call_id, "run_id": request.run_id, "evaluation_id": evaluation_id,
            "provider": response.provider, "model": response.model, "purpose": request.purpose,
            "prompt_hash": phash, "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens, "latency_ms": latency_ms,
        }  # fmt: skip
        with self._sf() as s, s.begin():
            s.add(m.LlmCall(**row))
