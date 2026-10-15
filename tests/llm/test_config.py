import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.llm.config import REDACTED, LLMConfig, redact, secret_values
from mailsim.llm.contract import DEFAULT_MODEL


def test_defaults_are_offline_stub():
    cfg = LLMConfig.from_env({})
    assert (cfg.provider, cfg.model, cfg.base_url) == ("stub", "stub-1", None)
    assert cfg.fallbacks is True and cfg.effort is None and cfg.budget.max_calls == 200


def test_anthropic_default_and_opus_selectable():
    assert LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "anthropic"}).model == DEFAULT_MODEL
    cfg = LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "anthropic",
                              "MAILSIM_LLM_MODEL": "claude-opus-5-5", "MAILSIM_LLM_EFFORT": "low",
                              "MAILSIM_LLM_FALLBACKS": "0", "MAILSIM_LLM_TIMEOUT_SECONDS": "30",
                              "MAILSIM_LLM_MAX_RETRIES": "1", "MAILSIM_LLM_MAX_CALLS": "3",
                              "MAILSIM_LLM_MAX_INPUT_TOKENS": "100",
                              "MAILSIM_LLM_MAX_OUTPUT_TOKENS": "50"})  # fmt: skip
    assert cfg.model == "claude-opus-5-5" and cfg.effort == "low" and cfg.fallbacks is False
    assert cfg.timeout_seconds == 30 and cfg.max_retries == 1
    assert (cfg.budget.max_calls, cfg.budget.max_input_tokens, cfg.budget.max_output_tokens) == (
        3, 100, 50,
    )  # fmt: skip


def test_openai_compatible_requirements():
    with pytest.raises(InvalidRequest, match="MODEL"):
        LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "openai_compatible"})
    with pytest.raises(InvalidRequest, match="BASE_URL"):
        LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "openai_compatible", "MAILSIM_LLM_MODEL": "m"})
    cfg = LLMConfig.from_env({"MAILSIM_LLM_PROVIDER": "openai_compatible",
                              "MAILSIM_LLM_MODEL": "llama3.1",
                              "MAILSIM_OLLAMA_URL": "http://127.0.0.1:11434/v1"})  # fmt: skip
    assert cfg.base_url == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize(
    "env, match",
    [
        ({"MAILSIM_LLM_PROVIDER": "openai"}, "MAILSIM_LLM_PROVIDER"),
        ({"MAILSIM_LLM_EFFORT": "extreme"}, "EFFORT"),
        ({"MAILSIM_LLM_MAX_CALLS": "many"}, "integer"),
        ({"MAILSIM_LLM_TIMEOUT_SECONDS": "0"}, "invalid LLM configuration"),
        ({"MAILSIM_LLM_MAX_CALLS": "-1"}, "invalid LLM configuration"),
    ],
)
def test_invalid_env(env, match):
    with pytest.raises(InvalidRequest, match=match):
        LLMConfig.from_env(env)


def test_from_process_env(monkeypatch):
    monkeypatch.setenv("MAILSIM_LLM_PROVIDER", "stub")
    monkeypatch.setenv("MAILSIM_LLM_MODEL", "stub-2")
    assert LLMConfig.from_env().model == "stub-2"


def test_redact_env_secrets_and_patterns(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "plain-secret-value-123")
    monkeypatch.setenv("MAILSIM_LLM_API_KEY", "short")  # too short to be treated as a secret
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert secret_values() == ["plain-secret-value-123"]
    text = (
        "key plain-secret-value-123; sk-ant-api03-AbCdEf123456; sk-abcdefghijklmnop1234; "
        'Authorization: Bearer abc.def.ghi; x-api-key: zzz999; {"api_key": "qqq"}'
    )
    out = redact(text, ["extra-token", ""])
    for leaked in ("plain-secret-value-123", "sk-ant-api03", "sk-abcdefghijklmnop", "abc.def.ghi",
                   "zzz999", "qqq"):  # fmt: skip
        assert leaked not in out
    assert out.count(REDACTED) >= 6 and "x-api-key: " in out
    assert redact("nothing here") == "nothing here"
    assert secret_values({"OPENAI_API_KEY": "abcdefghij"}) == ["abcdefghij"]
