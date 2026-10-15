import logging

import pytest
from sqlalchemy import select

from mailsim.domain.errors import ErrorCode, InvalidRequest, SimulationLimitExceeded
from mailsim.llm.config import LLMConfig
from mailsim.llm.contract import Budget, LLMRequest, Message
from mailsim.llm.gateway import (
    LLMGateway,
    LLMProviderError,
    LLMUnavailable,
    build_provider,
    prompt_hash,
    provider_error,
)
from mailsim.llm.providers.anthropic import AnthropicProvider
from mailsim.llm.providers.openai_compatible import OpenAICompatibleProvider
from mailsim.llm.providers.stub import StubProvider
from mailsim.store import models as m
from tests.llm.conftest import STUB_FIXTURES, ScriptedProvider

SECRET = "sk-ant-api03-TOPSECRETvalue0001"


def req(run_id=None, max_tokens=100, content="hi"):
    return LLMRequest(purpose="persona", messages=[Message(role="user", content=content)],
                      max_tokens=max_tokens, run_id=run_id)  # fmt: skip


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


def test_in_memory_budget_calls_and_tokens():
    p = ScriptedProvider(in_tokens=40, out_tokens=30)
    gw = LLMGateway(p, budget=Budget(max_calls=5, max_input_tokens=1000, max_output_tokens=50))
    gw.call(req("r1", max_tokens=100))
    assert p.requests[0].max_tokens == 50, "capped to the remaining output budget"
    gw.call(req("r1", max_tokens=100))
    assert p.requests[1].max_tokens == 20
    assert gw.usage("r1") == {"calls": 2, "input_tokens": 80, "output_tokens": 60}
    with pytest.raises(SimulationLimitExceeded) as ei:
        gw.call(req("r1"))
    assert ei.value.code == ErrorCode.SIMULATION_LIMIT_EXCEEDED
    assert ei.value.details["limits"] == ["max_output_tokens"]
    assert p.calls == 2, "the provider is never called once the budget is exhausted"
    gw.call(req("r2"))  # budgets are per run
    for _ in range(3):
        gw.call(req(None))  # no run: not budgeted, not counted
    assert gw.usage("none") == {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def test_call_and_input_budgets():
    gw = LLMGateway(ScriptedProvider(in_tokens=10), budget=Budget(max_calls=1))
    gw.call(req("r"))
    with pytest.raises(SimulationLimitExceeded, match="max_calls"):
        gw.call(req("r"))
    gw = LLMGateway(ScriptedProvider(in_tokens=10), budget=Budget(max_input_tokens=10))
    gw.call(req("r"))
    with pytest.raises(SimulationLimitExceeded, match="max_input_tokens"):
        gw.call(req("r"))


def test_errors_are_typed_and_redacted(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    caplog.set_level(logging.DEBUG, logger="mailsim.llm")
    boom = RuntimeError(f"connection failed for key {SECRET}")
    typed = LLMUnavailable(f"upstream echoed {SECRET}")
    gw = LLMGateway(ScriptedProvider(boom, typed, "fine"))
    with pytest.raises(LLMProviderError) as ei:
        gw.call(req("r"))
    assert ei.value.details == {"provider": "stub", "kind": "unexpected", "retryable": False}
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    with pytest.raises(LLMUnavailable) as ei2:
        gw.call(req("r"))
    assert SECRET not in ei2.value.message and ei2.value.code == ErrorCode.RATE_LIMITED
    gw.call(req("r", content=f"prompt mentioning {SECRET}"))
    assert SECRET not in caplog.text and "prompt mentioning" not in caplog.text
    assert "llm call failed" in caplog.text and "llm call provider=stub" in caplog.text


def test_provider_error_kinds():
    err = provider_error("anthropic", "auth", f"bad key {SECRET}", retryable=False)
    assert isinstance(err, InvalidRequest) and SECRET not in err.message
    assert isinstance(provider_error("x", "timeout", "t", retryable=True), LLMUnavailable)


@pytest.mark.db
def test_llm_calls_are_recorded_and_budget_is_read_from_the_db(env):
    run = env.run()
    ticks = iter([1.0, 1.25, 2.0, 2.5])
    p = ScriptedProvider(in_tokens=7, out_tokens=3)
    gw = LLMGateway(p, session_factory=env.session_factory, budget=Budget(max_calls=2),
                    clock=lambda: next(ticks))  # fmt: skip
    r1 = gw.call(req(run), evaluation_id="ev_1")
    assert r1.latency_ms == 250 and r1.prompt_hash == prompt_hash(req(run))
    with env.session_factory() as s:
        row = s.execute(select(m.LlmCall).where(m.LlmCall.id == r1.llm_call_id)).scalar_one()
        assert (row.run_id, row.evaluation_id, row.provider, row.model, row.purpose) == (
            run, "ev_1", "stub", "fake-1", "persona",
        )  # fmt: skip
        assert (row.prompt_hash, row.input_tokens, row.output_tokens, row.latency_ms) == (
            r1.prompt_hash, 7, 3, 250,
        )  # fmt: skip
    gw.call(req(run))
    # a fresh gateway (another process) sees the same per-run usage
    fresh = LLMGateway(ScriptedProvider(), session_factory=env.session_factory,
                       budget=Budget(max_calls=2))  # fmt: skip
    assert fresh.usage(run) == {"calls": 2, "input_tokens": 14, "output_tokens": 6}
    with pytest.raises(SimulationLimitExceeded):
        fresh.call(req(run))
