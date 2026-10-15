"""Provider-neutral LLM gateway (WS-G).

The gateway is the only module that touches concrete providers. It

- selects a provider from configuration (``MAILSIM_LLM_PROVIDER``; offline ``stub`` default),
- enforces the per-run :class:`Budget` with a RESERVATION: under a per-run lock it checks the
  recorded usage (every ``llm_calls`` row of the run, ok and failed) plus the estimated
  input (chars / 4) and the requested output, then inserts the call row up front with
  ``status="failed"`` and the reserved amounts. After the provider returns, the row is
  updated with the real usage and status. A crash therefore leaves a conservative
  (charged) row, never an unaccounted call. ``max_tokens`` is capped to the remaining
  output budget, and a call is refused when that remainder is below ``MIN_OUTPUT_TOKENS``.
- counts failures: a provider error with a billed response (refusal, malformed, truncated
  or empty output) keeps its reported usage; a transport failure with no response keeps
  zero tokens but still counts as a call (it consumed retries).
- maps every provider failure to a typed :class:`SimError` with secrets redacted.

Budget scope: per RUN (llm_calls.run_id), across reset epochs - a reset does not refund
model usage. Model OUTPUTS are not stored here: callers record them in run events, so
replay never calls a model (spec 6.3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mailsim.domain.errors import (
    InvalidRequest,
    SimError,
    SimulationLimitExceeded,
    UpstreamUnavailable,
)
from mailsim.domain.ids import new_control_id
from mailsim.llm.config import LLMConfig, redact
from mailsim.llm.contract import Budget, LLMProvider, LLMRequest, LLMResponse
from mailsim.store import models as m

log = logging.getLogger("mailsim.llm")
SessionFactory = Callable[[], Session]
MIN_OUTPUT_TOKENS = 256
TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length"})
RUN_SCOPED_PURPOSES = frozenset({"persona", "judge"})


class LLMUnavailable(UpstreamUnavailable):
    """Transient provider failure (timeout, connection, 408/429/overloaded, 5xx). HTTP 503."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, details={"retryable": True, **(details or {})})


class LLMProviderError(UpstreamUnavailable):
    """Non-retryable provider failure (auth, rejected request, refusal, bad output). HTTP 503:
    it is the provider's (or the operator's configuration's) fault, never the API caller's."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, details={"retryable": False, **(details or {})})


def provider_error(provider: str, kind: str, message: str, *, retryable: bool,
                   usage: tuple[int, int] | None = None) -> SimError:  # fmt: skip
    """Typed error for a provider failure (message always redacted). `usage` = the billed
    (input, output) tokens when the provider did return a response."""
    cls = LLMUnavailable if retryable else LLMProviderError
    details: dict[str, object] = {"provider": provider, "kind": kind, "retryable": retryable}
    if usage is not None:
        details["input_tokens"], details["output_tokens"] = usage
    return cls(redact(f"{provider} provider failed ({kind}): {message}")[:500], details=details)


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


def estimate_input_tokens(request: LLMRequest) -> int:
    return max(1, math.ceil(len(canonical_prompt(request)) / 4))


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


@dataclass
class _MemRun:
    rows: dict[str, tuple[int, int]] = field(default_factory=dict)


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
        self._mem: dict[str, _MemRun] = {}  # used only without a session factory
        self._mem_lock = threading.Lock()

    @classmethod
    def from_config(
        cls, config: LLMConfig | None = None, *, session_factory: SessionFactory | None = None
    ) -> LLMGateway:
        cfg = config or LLMConfig.from_env()
        return cls(build_provider(cfg), session_factory=session_factory, budget=cfg.budget)

    @property
    def _provider_name(self) -> str:
        return str(getattr(self.provider, "name", "unknown"))

    # ------------------------------------------------------------------ budget

    def usage(self, run_id: str) -> dict[str, int]:
        if self._sf is None:
            u = self._mem_usage(run_id)
        else:
            with self._sf() as s:
                u = self._db_usage(s, run_id)
        return {"calls": u.calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens}

    def _mem_usage(self, run_id: str) -> _Usage:
        rows = self._mem.get(run_id, _MemRun()).rows.values()
        return _Usage(len(rows), sum(r[0] for r in rows), sum(r[1] for r in rows))

    @staticmethod
    def _db_usage(s: Session, run_id: str) -> _Usage:
        row = s.execute(
            select(
                func.count(m.LlmCall.id),
                func.coalesce(func.sum(m.LlmCall.input_tokens), 0),
                func.coalesce(func.sum(m.LlmCall.output_tokens), 0),
            ).where(m.LlmCall.run_id == run_id)
        ).one()
        return _Usage(int(row[0]), int(row[1]), int(row[2]))

    def _admit(self, request: LLMRequest, u: _Usage) -> LLMRequest:
        b, estimate = self.budget, estimate_input_tokens(request)
        remaining_out = b.max_output_tokens - u.output_tokens
        exceeded = [name for name, over in (
            ("max_calls", u.calls >= b.max_calls),
            ("max_input_tokens", u.input_tokens + estimate > b.max_input_tokens),
            ("max_output_tokens", remaining_out < min(MIN_OUTPUT_TOKENS, request.max_tokens)),
        ) if over]  # fmt: skip
        if exceeded:
            raise SimulationLimitExceeded(
                f"LLM budget exhausted for run: {', '.join(exceeded)}",
                details={
                    "limits": exceeded,
                    "calls": u.calls,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                },  # fmt: skip
            )
        if request.max_tokens > remaining_out:
            return request.model_copy(update={"max_tokens": remaining_out})
        return request

    def _reserve(self, request: LLMRequest, call_id: str, phash: str,
                 evaluation_id: str | None) -> LLMRequest:  # fmt: skip
        """Check + reserve atomically per run; returns the (possibly capped) request."""
        run_id = request.run_id
        if self._sf is None:
            with self._mem_lock:
                if run_id is not None:
                    request = self._admit(request, self._mem_usage(run_id))
                    reserved = (estimate_input_tokens(request), request.max_tokens)
                    self._mem.setdefault(run_id, _MemRun()).rows[call_id] = reserved
            return request
        with self._sf() as s, s.begin():
            if run_id is not None:
                # serialise reservations of one run (released at commit; the model call
                # itself runs outside any transaction or lock)
                s.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"llm:{run_id}"))))
                request = self._admit(request, self._db_usage(s, run_id))
            s.add(m.LlmCall(
                id=call_id, run_id=run_id, evaluation_id=evaluation_id,
                provider=self._provider_name, model=str(getattr(self.provider, "model", "")),
                purpose=request.purpose, prompt_hash=phash,
                input_tokens=estimate_input_tokens(request), output_tokens=request.max_tokens,
                latency_ms=0, status="failed",
            ))  # fmt: skip
        return request

    def _settle(self, call_id: str, run_id: str | None, *, status: str, tokens: tuple[int, int],
                latency_ms: int, model: str | None = None) -> None:  # fmt: skip
        if self._sf is None:
            with self._mem_lock:
                if run_id is not None:
                    self._mem[run_id].rows[call_id] = tokens
            return
        values: dict[str, Any] = {"status": status, "input_tokens": tokens[0],
                                  "output_tokens": tokens[1], "latency_ms": latency_ms}  # fmt: skip
        if model:
            values["model"] = model
        with self._sf() as s, s.begin():
            s.execute(update(m.LlmCall).where(m.LlmCall.id == call_id).values(**values))

    # ------------------------------------------------------------------ calls

    def call(self, request: LLMRequest, *, evaluation_id: str | None = None) -> GatewayResult:
        if request.purpose in RUN_SCOPED_PURPOSES and request.run_id is None:
            raise InvalidRequest(f"run_id is required for {request.purpose} LLM calls")
        phash = prompt_hash(request)
        call_id = new_control_id("llm")
        request = self._reserve(request, call_id, phash, evaluation_id)
        started = self._clock()
        try:
            response = self._checked(self.provider.generate(request))
        except Exception as exc:
            ms = max(0, int((self._clock() - started) * 1000))
            err = self._typed(exc)
            usage = (int(str(err.details.get("input_tokens", 0))),
                     int(str(err.details.get("output_tokens", 0))))  # fmt: skip
            self._settle(call_id, request.run_id, status="failed", tokens=usage, latency_ms=ms)
            err.details["llm_call_id"] = call_id
            log.warning("llm call failed provider=%s purpose=%s run=%s code=%s kind=%s",
                        self._provider_name, request.purpose, request.run_id, err.code,
                        err.details.get("kind"))  # fmt: skip
            raise err from None
        ms = max(0, int((self._clock() - started) * 1000))
        self._settle(call_id, request.run_id, status="ok", latency_ms=ms, model=response.model,
                     tokens=(response.input_tokens, response.output_tokens))  # fmt: skip
        log.info(
            "llm call provider=%s model=%s purpose=%s run=%s prompt_hash=%s in=%d out=%d ms=%d",
            response.provider, response.model, request.purpose, request.run_id, phash[:16],
            response.input_tokens, response.output_tokens, ms,
        )  # fmt: skip
        return GatewayResult(response, call_id, phash, ms)

    def _checked(self, response: LLMResponse) -> LLMResponse:
        """Billed-but-unusable responses are failures (and keep their usage)."""
        usage = (response.input_tokens, response.output_tokens)
        if response.stop_reason in TRUNCATED_STOP_REASONS:
            raise provider_error(response.provider, "truncated",
                                 f"output hit the token limit (stop_reason={response.stop_reason})",
                                 retryable=False, usage=usage)  # fmt: skip
        if not response.text.strip():
            raise provider_error(response.provider, "empty_output", "no text content",
                                 retryable=False, usage=usage)  # fmt: skip
        return response

    def _typed(self, exc: Exception) -> SimError:
        if isinstance(exc, SimError):
            exc.message = redact(exc.message)
            exc.args = tuple(redact(str(a)) for a in exc.args)
            return exc
        # any SDK/transport surprise becomes a typed error (no raw text: it may hold secrets)
        return provider_error(self._provider_name, "unexpected", type(exc).__name__,
                              retryable=False)  # fmt: skip

    def generate(self, request: LLMRequest) -> LLMResponse:
        """LLMProvider-shaped convenience: budgeted + recorded, returns the response only."""
        return self.call(request).response
