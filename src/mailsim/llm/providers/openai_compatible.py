"""OpenAI-compatible chat-completions provider (e.g. Ollama at ``http://127.0.0.1:11434/v1``).

Network egress: one of the two reviewed exemptions from the network-client ban. In
deployment the client honours ``HTTPS_PROXY``/``HTTP_PROXY`` so traffic leaves only through the
allowlist egress proxy. The optional bearer key comes from ``MAILSIM_LLM_API_KEY`` and is
never logged or echoed in errors.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from mailsim.domain.errors import SimError
from mailsim.llm.contract import LLMRequest, LLMResponse, ProviderName


class OpenAICompatibleProvider:
    name: ProviderName = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._transport = transport

    def _client(self) -> httpx.Client:
        key = self._api_key if self._api_key is not None else os.environ.get("MAILSIM_LLM_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx.Client(timeout=self.timeout_seconds, headers=headers,
                            transport=self._transport, trust_env=True)  # fmt: skip

    def _fail(self, kind: str, message: str, *, retryable: bool,
              usage: tuple[int, int] | None = None) -> SimError:  # fmt: skip
        from mailsim.llm.gateway import provider_error

        return provider_error(self.name, kind, message, retryable=retryable, usage=usage)

    def generate(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages += [{"role": x.role, "content": x.content} for x in request.messages]
        body: dict[str, Any] = {"model": self.model, "messages": messages,
                                "max_tokens": request.max_tokens, "stream": False}  # fmt: skip
        if request.temperature is not None:
            body["temperature"] = request.temperature
        try:
            with self._client() as client:  # one short-lived client per call: always closed
                resp = client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException:
            raise self._fail("timeout", "request timed out", retryable=True) from None
        except httpx.HTTPError as exc:
            raise self._fail("connection", type(exc).__name__, retryable=True) from None
        if resp.status_code in (408, 429) or resp.status_code >= 500:
            kind = {408: "timeout", 429: "rate_limited"}.get(resp.status_code, "server_error")
            raise self._fail(kind, f"HTTP {resp.status_code}", retryable=True)
        if resp.status_code >= 400:
            kind = "auth" if resp.status_code in (401, 403) else "bad_request"
            raise self._fail(kind, f"HTTP {resp.status_code}", retryable=False)
        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("content is not a string")
            usage = data.get("usage") or {}
            return LLMResponse(
                text=text,
                provider=self.name,
                model=str(data.get("model") or self.model),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                stop_reason=choice.get("finish_reason"),
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._fail("bad_response", "malformed completion payload",
                             retryable=False, usage=(0, 0)) from None  # fmt: skip
