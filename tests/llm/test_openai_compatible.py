"""OpenAI-compatible provider against httpx.MockTransport only (no sockets)."""

import json

import httpx
import pytest

from mailsim.llm.contract import LLMRequest, Message
from mailsim.llm.gateway import LLMProviderError, LLMUnavailable
from mailsim.llm.providers.openai_compatible import OpenAICompatibleProvider
from tests.llm.conftest import fake_key

KEY = fake_key("ollama-")


def req(**kw):
    return LLMRequest(purpose="persona", system=kw.pop("system", "be brief"),
                      messages=[Message(role="user", content="hi")], **kw)  # fmt: skip


def provider(handler, **kw):
    return OpenAICompatibleProvider(base_url="http://ollama.test/v1/", model="llama3.1",
                                    transport=httpx.MockTransport(handler), **kw)  # fmt: skip


def ok(payload):
    return lambda request: httpx.Response(200, json=payload)


def test_success_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3.1:8b",
            "choices": [{"message": {"role": "assistant", "content": "Sure."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })  # fmt: skip

    r = provider(handler, api_key=KEY).generate(req(temperature=0.2, max_tokens=50))
    assert seen["url"] == "http://ollama.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {
        "model": "llama3.1", "max_tokens": 50, "stream": False, "temperature": 0.2,
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
    }  # fmt: skip
    assert (r.text, r.provider, r.model, r.input_tokens, r.output_tokens, r.stop_reason) == (
        "Sure.", "openai_compatible", "llama3.1:8b", 12, 3, "stop",
    )  # fmt: skip


def test_no_key_no_system_no_usage(monkeypatch):
    monkeypatch.delenv("MAILSIM_LLM_API_KEY", raising=False)
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    r = provider(handler).generate(req(system=""))
    assert seen["auth"] is None and seen["body"]["messages"][0]["role"] == "user"
    assert "temperature" not in seen["body"]
    assert (r.model, r.input_tokens, r.output_tokens, r.stop_reason) == ("llama3.1", 0, 0, None)


def test_env_key_is_used(monkeypatch):
    monkeypatch.setenv("MAILSIM_LLM_API_KEY", KEY)
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    provider(handler).generate(req())
    assert seen["auth"] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    "status, cls, kind",
    [(429, LLMUnavailable, "rate_limited"), (503, LLMUnavailable, "server_error"),
     (408, LLMUnavailable, "timeout"),
     (401, LLMProviderError, "auth"), (403, LLMProviderError, "auth"),
     (400, LLMProviderError, "bad_request")],
)  # fmt: skip
def test_status_mapping(status, cls, kind):
    def handler(request):
        return httpx.Response(status, json={"error": f"bad key {KEY}"})

    with pytest.raises(cls) as ei:
        provider(handler, api_key=KEY).generate(req())
    assert ei.value.details["kind"] == kind and KEY not in ei.value.message


@pytest.mark.parametrize(
    "exc, kind",
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("refused"), "connection")],
)
def test_transport_errors(exc, kind):
    def handler(request):
        raise exc

    with pytest.raises(LLMUnavailable) as ei:
        provider(handler).generate(req())
    assert ei.value.details["kind"] == kind


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="not json"), httpx.Response(200, json={"choices": []}),
     httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
     httpx.Response(200, json={"choices": [{"message": {"content": "x"}}],
                               "usage": {"prompt_tokens": "lots"}})],
)  # fmt: skip
def test_malformed_payloads(response):
    with pytest.raises(LLMProviderError) as ei:
        provider(lambda request: response).generate(req())
    assert ei.value.details["kind"] == "bad_response"
    assert (ei.value.details["input_tokens"], ei.value.details["output_tokens"]) == (0, 0)


def test_client_is_closed_after_each_call():
    closed = []

    class Tracking(httpx.MockTransport):
        def close(self):
            closed.append(True)

    p = OpenAICompatibleProvider(base_url="https://llm.example.test/v1", model="m",
                                 transport=Tracking(ok({"choices": [{"message": {"content": "x"}}]})))  # fmt: skip
    p.generate(req())
    p.generate(req())
    assert closed == [True, True]
