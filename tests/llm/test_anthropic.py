"""Anthropic provider: the real SDK client over httpx2.MockTransport (no sockets), plus
small client stubs for paths the transport cannot reach."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from mailsim.llm.contract import LLMRequest, Message
from mailsim.llm.gateway import LLMProviderError, LLMUnavailable
from mailsim.llm.providers import anthropic as provider_mod
from mailsim.llm.providers.anthropic import FALLBACK_BETA, AnthropicProvider

KEY = "sk-ant-api03-FAKEKEYFORTESTS00001"


def req(system="be brief"):
    return LLMRequest(purpose="persona", system=system,
                      messages=[Message(role="user", content="hi")], max_tokens=300)  # fmt: skip


def message(text="Hello there.", stop="end_turn", content=None, model="claude-sonnet-5-5"):
    blocks = content if content is not None else [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": text},
    ]  # fmt: skip
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
            "content": blocks, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 21, "output_tokens": 4}}  # fmt: skip


def sdk_client(handler):
    return anthropic.Anthropic(
        api_key=KEY, max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )  # fmt: skip


def capture(payload, status=200):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["beta"] = request.headers.get("anthropic-beta")
        seen["body"] = json.loads(request.content)
        return httpx2.Response(status, json=payload)

    return seen, handler


def test_default_model_uses_beta_fallbacks_and_no_temperature():
    seen, handler = capture(message())
    p = AnthropicProvider(client=sdk_client(handler), effort="low")
    r = p.generate(req().model_copy(update={"temperature": 0.3}))
    assert seen["path"] == "/v1/messages" and FALLBACK_BETA in (seen["beta"] or "")
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 300
    assert body["fallbacks"] == "default" and body["output_config"] == {"effort": "low"}
    assert body["system"] == "be brief" and "temperature" not in body
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert (r.text, r.provider, r.model, r.input_tokens, r.output_tokens, r.stop_reason) == (
        "Hello there.", "anthropic", "claude-sonnet-5-5", 21, 4, "end_turn",
    )  # fmt: skip


def test_fallbacks_disabled_or_unsupported_model_uses_plain_messages():
    for kw in ({"fallbacks": False}, {"model": "claude-haiku-5-5"}):
        seen, handler = capture(message(model=kw.get("model", "claude-sonnet-5-5")))
        AnthropicProvider(client=sdk_client(handler), **kw).generate(req(system=""))
        assert "fallbacks" not in seen["body"] and "system" not in seen["body"]
        assert "output_config" not in seen["body"] and FALLBACK_BETA not in (seen["beta"] or "")


@pytest.mark.parametrize(
    "status, cls, kind",
    [(429, LLMUnavailable, "rate_limited"), (500, LLMUnavailable, "server_error"),
     (529, LLMUnavailable, "server_error"), (401, LLMProviderError, "auth"),
     (403, LLMProviderError, "auth"), (400, LLMProviderError, "bad_request"),
     (404, LLMProviderError, "bad_request")],
)  # fmt: skip
def test_status_mapping(status, cls, kind):
    payload = {"type": "error", "error": {"type": "x", "message": f"key {KEY} rejected"}}
    _, handler = capture(payload, status)
    with pytest.raises(cls) as ei:
        AnthropicProvider(client=sdk_client(handler)).generate(req())
    assert ei.value.details == {
        "provider": "anthropic",
        "kind": kind,
        "retryable": cls is LLMUnavailable,
    }
    assert KEY not in ei.value.message


@pytest.mark.parametrize(
    "exc, kind",
    [(httpx2.ConnectTimeout("slow"), "timeout"), (httpx2.ConnectError("refused"), "connection")],
)
def test_transport_errors(exc, kind):
    def handler(request):
        raise exc

    with pytest.raises(LLMUnavailable) as ei:
        AnthropicProvider(client=sdk_client(handler)).generate(req())
    assert ei.value.details["kind"] == kind


def test_refusal_and_empty_output_are_typed_errors():
    payload = message(stop="refusal")
    payload["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "x"}
    _, handler = capture(payload)
    with pytest.raises(LLMProviderError, match="category=cyber") as ei:
        AnthropicProvider(client=sdk_client(handler)).generate(req())
    assert ei.value.details["kind"] == "refusal"
    _, handler = capture(message(content=[], stop="max_tokens"))
    with pytest.raises(LLMProviderError, match="stop_reason=max_tokens"):
        AnthropicProvider(client=sdk_client(handler)).generate(req())


class _Raising:
    def __init__(self, exc=None, result=None):
        self.exc, self.result = exc, result
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        if self.exc:
            raise self.exc
        return self.result


def test_other_sdk_errors_and_malformed_payload():
    with pytest.raises(LLMProviderError) as ei:
        AnthropicProvider(client=_Raising(anthropic.AnthropicError("odd"))).generate(req())
    assert ei.value.details["kind"] == "client_error"
    bad = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="x")],
                          usage=None)  # fmt: skip
    with pytest.raises(LLMProviderError) as ei:
        AnthropicProvider(client=_Raising(result=bad)).generate(req())
    assert ei.value.details["kind"] == "bad_response"
    no_model = SimpleNamespace(stop_reason="end_turn", stop_details=None, model=None,
                               content=[SimpleNamespace(type="text", text=" hi ")],
                               usage=SimpleNamespace(input_tokens=None, output_tokens=2))  # fmt: skip
    r = AnthropicProvider(model="claude-opus-5-5", client=_Raising(result=no_model)).generate(req())
    assert (r.text, r.model, r.input_tokens) == ("hi", "claude-opus-5-5", 0)


def test_lazy_client_reads_env_and_maps_construction_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    p = AnthropicProvider(timeout_seconds=5, max_retries=1)
    client = p._get_client()
    assert isinstance(client, anthropic.Anthropic) and p._get_client() is client
    assert client.max_retries == 1

    def broken(**kw):
        raise anthropic.AnthropicError(f"no credentials ({KEY})")

    monkeypatch.setattr(provider_mod.anthropic, "Anthropic", broken)
    with pytest.raises(LLMProviderError) as ei:
        AnthropicProvider().generate(req())
    assert ei.value.details["kind"] == "auth" and KEY not in ei.value.message
