import logging
import threading

import pytest
from sqlalchemy import select

from mailsim.domain.errors import ErrorCode, InvalidRequest, SimulationLimitExceeded
from mailsim.llm.config import LLMConfig
from mailsim.llm.contract import Budget, LLMRequest, LLMResponse, Message
from mailsim.llm.gateway import (
    MIN_OUTPUT_TOKENS,
    LLMGateway,
    LLMProviderError,
    LLMUnavailable,
    build_provider,
    estimate_input_tokens,
    prompt_hash,
    provider_error,
)
from mailsim.llm.providers.anthropic import AnthropicProvider
from mailsim.llm.providers.openai_compatible import OpenAICompatibleProvider
from mailsim.llm.providers.stub import StubProvider
from mailsim.store import models as m
from tests.llm.conftest import STUB_FIXTURES, ScriptedProvider, fake_key

SECRET = fake_key()


def req(run_id=None, max_tokens=300, content="hi", purpose="generation"):
    return LLMRequest(purpose=purpose, messages=[Message(role="user", content=content)],
                      max_tokens=max_tokens, run_id=run_id)  # fmt: skip


def resp(text="ok", stop="end_turn", i=10, o=5):
    return LLMResponse(text=text, provider="stub", model="fake-1", input_tokens=i,
                       output_tokens=o, stop_reason=stop)  # fmt: skip


def test_build_provider_per_config():
    assert isinstance(build_provider(LLMConfig()), StubProvider)
    stub = build_provider(LLMConfig(stub_fixtures=str(STUB_FIXTURES)))
    assert isinstance(stub, StubProvider) and stub.responses
    a = build_provider(LLMConfig(provider="anthropic", model="claude-opus-5-5", effort="low"))
    assert isinstance(a, AnthropicProvider) and a.model == "claude-opus-5-5" and a.fallbacks
    o = build_provider(LLMConfig(provider="openai_compatible", model="llama3.1",
                                 base_url="http://127.0.0.1:11434/v1/"))  # fmt: skip
    assert isinstance(o, OpenAICompatibleProvider) and o.base_url.endswith("/v1")


def test_from_config_uses_env_budget(monkeypatch):
    monkeypatch.setenv("MAILSIM_LLM_PROVIDER", "stub")
    monkeypatch.setenv("MAILSIM_LLM_MAX_CALLS", "1")
    gw = LLMGateway.from_config()
    assert isinstance(gw.provider, StubProvider) and gw.budget.max_calls == 1
    gw.generate(req("run_a"))
    with pytest.raises(SimulationLimitExceeded):
        gw.generate(req("run_a"))


def test_run_id_required_for_persona_and_judge():
    p = ScriptedProvider()
    gw = LLMGateway(p)
    for purpose in ("persona", "judge"):
        with pytest.raises(InvalidRequest, match="run_id is required"):
            gw.call(req(None, purpose=purpose))
    gw.call(req(None))  # generation may run outside a run (not budgeted)
    assert p.calls == 1


def test_output_budget_caps_and_minimum():
    p = ScriptedProvider(in_tokens=1, out_tokens=200)
    gw = LLMGateway(p, budget=Budget(max_output_tokens=700))
    gw.call(req("r1", max_tokens=1000))
    assert p.requests[0].max_tokens == 700, "capped to the remaining output budget"
    gw.call(req("r1", max_tokens=1000))
    assert p.requests[1].max_tokens == 500
    gw.call(req("r1", max_tokens=1000))  # 300 left >= MIN_OUTPUT_TOKENS
    assert p.requests[2].max_tokens == 300
    with pytest.raises(SimulationLimitExceeded) as ei:  # 100 left < 256
        gw.call(req("r1", max_tokens=1000))
    assert ei.value.details["limits"] == ["max_output_tokens"] and p.calls == 3
    gw.call(req("r1", max_tokens=50))  # small requests may use what is left
    assert p.requests[3].max_tokens == 50 and MIN_OUTPUT_TOKENS == 256
    assert gw.usage("r1") == {"calls": 4, "input_tokens": 4, "output_tokens": 800}
    gw.call(req("r2"))  # budgets are per run
    assert gw.usage("none") == {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def test_call_and_estimated_input_budgets():
    gw = LLMGateway(ScriptedProvider(in_tokens=10), budget=Budget(max_calls=1))
    gw.call(req("r"))
    with pytest.raises(SimulationLimitExceeded, match="max_calls") as ei:
        gw.call(req("r"))
    assert ei.value.code == ErrorCode.SIMULATION_LIMIT_EXCEEDED
    big = req("r2", content="x" * 4000)
    p = ScriptedProvider()
    gw = LLMGateway(p, budget=Budget(max_input_tokens=estimate_input_tokens(big) - 1))
    with pytest.raises(SimulationLimitExceeded, match="max_input_tokens"):
        gw.call(big)
    assert p.calls == 0, "refused on the estimate, before any spend"


def test_failed_calls_count_with_billed_usage():
    p = ScriptedProvider(
        resp("", i=7, o=3),  # empty output
        resp("cut off", stop="max_tokens", i=11, o=300),  # truncated
        resp("cut", stop="length", i=1, o=1),  # openai-style truncation
        provider_error("anthropic", "refusal", "declined", retryable=False, usage=(5, 2)),
        LLMUnavailable("timeout"),  # no response: zero tokens, but a call
        "fine",
    )
    gw = LLMGateway(p, budget=Budget(max_calls=6))
    kinds = []
    for _ in range(5):
        with pytest.raises((LLMProviderError, LLMUnavailable)) as ei:
            gw.call(req("r"))
        kinds.append(ei.value.details.get("kind"))
        assert ei.value.details["llm_call_id"].startswith("llm_")
    assert kinds == ["empty_output", "truncated", "truncated", "refusal", None]
    assert gw.usage("r") == {"calls": 5, "input_tokens": 24, "output_tokens": 306}
    gw.call(req("r"))
    with pytest.raises(SimulationLimitExceeded):
        gw.call(req("r"))


def test_errors_are_typed_and_redacted(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    caplog.set_level(logging.DEBUG, logger="mailsim.llm")
    boom = RuntimeError(f"connection failed for key {SECRET}")
    typed = LLMUnavailable(f"upstream echoed {SECRET}")
    gw = LLMGateway(ScriptedProvider(boom, typed, "fine"))
    with pytest.raises(LLMProviderError) as ei:
        gw.call(req("r"))
    assert ei.value.details["kind"] == "unexpected" and ei.value.details["provider"] == "stub"
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    with pytest.raises(LLMUnavailable) as ei2:
        gw.call(req("r"))
    assert SECRET not in ei2.value.message and ei2.value.code == ErrorCode.RATE_LIMITED
    assert all(SECRET not in str(a) for a in ei2.value.args) and SECRET not in str(ei2.value)
    gw.call(req("r", content=f"prompt mentioning {SECRET}"))
    assert SECRET not in caplog.text and "prompt mentioning" not in caplog.text
    assert "llm call failed" in caplog.text and "llm call provider=stub" in caplog.text


def test_provider_error_kinds():
    err = provider_error("anthropic", "auth", f"bad key {SECRET}", retryable=False)
    assert isinstance(err, InvalidRequest) and SECRET not in err.message
    assert "input_tokens" not in err.details
    assert isinstance(provider_error("x", "timeout", "t", retryable=True), LLMUnavailable)


def test_in_memory_reservations_are_serialised():
    """Concurrent callers on one run never overshoot max_calls."""
    gate = threading.Barrier(8)
    p = ScriptedProvider(*["ok"] * 8)
    gw = LLMGateway(p, budget=Budget(max_calls=3))
    results = []

    def worker():
        gate.wait()
        try:
            gw.call(req("r"))
            results.append("ok")
        except SimulationLimitExceeded:
            results.append("limit")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 3 and results.count("limit") == 5


@pytest.mark.db
def test_llm_calls_are_recorded_and_budget_is_read_from_the_db(env):
    run = env.run()
    ticks = iter([1.0, 1.25, 2.0, 2.5, 3.0, 3.5])
    p = ScriptedProvider(resp(i=7, o=3), resp(i=7, o=3), resp("", i=4, o=1))
    gw = LLMGateway(p, session_factory=env.session_factory, budget=Budget(max_calls=3),
                    clock=lambda: next(ticks))  # fmt: skip
    r1 = gw.call(req(run, purpose="persona"), evaluation_id="ev_1")
    assert r1.latency_ms == 250 and r1.prompt_hash == prompt_hash(req(run, purpose="persona"))
    with env.session_factory() as s:
        row = s.execute(select(m.LlmCall).where(m.LlmCall.id == r1.llm_call_id)).scalar_one()
        assert (row.run_id, row.evaluation_id, row.provider, row.model, row.purpose) == (
            run, "ev_1", "stub", "fake-1", "persona",
        )  # fmt: skip
        assert (row.prompt_hash, row.input_tokens, row.output_tokens, row.latency_ms,
                row.status) == (r1.prompt_hash, 7, 3, 250, "ok")  # fmt: skip
    gw.call(req(run))
    with pytest.raises(LLMProviderError) as ei:
        gw.call(req(run))
    with env.session_factory() as s:
        failed = s.get(m.LlmCall, ei.value.details["llm_call_id"])
        assert (failed.status, failed.input_tokens, failed.output_tokens) == ("failed", 4, 1)
    # a fresh gateway (another process) sees the same per-run usage, ok + failed
    fresh = LLMGateway(ScriptedProvider(), session_factory=env.session_factory,
                       budget=Budget(max_calls=3))  # fmt: skip
    assert fresh.usage(run) == {"calls": 3, "input_tokens": 18, "output_tokens": 7}
    with pytest.raises(SimulationLimitExceeded):
        fresh.call(req(run))
    # a call outside any run is recorded (run_id NULL) but not budgeted
    outside = LLMGateway(ScriptedProvider(), session_factory=env.session_factory)
    rid = outside.call(req(None)).llm_call_id
    with env.session_factory() as s:
        assert s.get(m.LlmCall, rid).run_id is None


@pytest.mark.db
def test_reservation_row_is_conservative_while_in_flight(env):
    run = env.run()
    seen = {}

    class Peek(ScriptedProvider):
        def generate(self, request):
            with env.session_factory() as s:
                row = s.execute(select(m.LlmCall).where(m.LlmCall.run_id == run)).scalar_one()
                seen["row"] = (row.status, row.input_tokens, row.output_tokens)
            return super().generate(request)

    gw = LLMGateway(Peek(), session_factory=env.session_factory)
    r = req(run, max_tokens=400)
    gw.call(r)
    assert seen["row"] == ("failed", estimate_input_tokens(r), 400)
    assert gw.usage(run) == {"calls": 1, "input_tokens": 10, "output_tokens": 5}
