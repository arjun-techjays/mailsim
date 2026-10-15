"""Claude via the official Anthropic SDK (``anthropic`` 1.x).

- Credentials: the SDK resolves them itself (``ANTHROPIC_API_KEY`` first); this module never
  reads, stores, logs or echoes a key.
- Egress: one of the two reviewed exemptions from the network-client ban. In deployment the
  SDK's HTTP client honours ``HTTPS_PROXY``, which must point at the allowlist egress proxy
  (only ``api.anthropic.com`` allowed); the ``app`` service has no direct internet route.
- Models: default ``claude-sonnet-5-5``; ``claude-opus-5-5`` selectable via MAILSIM_LLM_MODEL.
  On these models thinking is adaptive by default and sampling parameters (``temperature``)
  are rejected, so ``temperature`` is never sent; depth is tuned with ``output_config.effort``.
- Refusals: server-side refusal fallbacks (``fallbacks="default"``, beta
  ``server-side-fallback-2026-07-01``) are enabled by default for the current models
  (disable with MAILSIM_LLM_FALLBACKS=0). A final ``stop_reason == "refusal"`` is a typed
  non-retryable error carrying the billed usage, never an empty persona message.
- ``ANTHROPIC_BASE_URL`` (read by the SDK) can redirect traffic; it is subject to the same
  egress proxy. The SDK's own transport (httpx2) is never imported here.
"""

from __future__ import annotations

from typing import Any

import anthropic

from mailsim.domain.errors import SimError
from mailsim.llm.contract import DEFAULT_MODEL, LLMRequest, LLMResponse, ProviderName

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
)


class AnthropicProvider:
    name: ProviderName = "anthropic"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        effort: str | None = None,
        fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.effort = effort
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        self._client = client

    def _fail(self, kind: str, message: str, *, retryable: bool,
              usage: tuple[int, int] | None = None) -> SimError:  # fmt: skip
        from mailsim.llm.gateway import provider_error

        return provider_error(self.name, kind, message, retryable=retryable, usage=usage)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                self._client = anthropic.Anthropic(
                    timeout=self.timeout_seconds, max_retries=self.max_retries
                )
            except anthropic.AnthropicError as exc:
                raise self._fail("auth", type(exc).__name__, retryable=False) from None
        return self._client

    def _params(self, request: LLMRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": x.role, "content": x.content} for x in request.messages],
        }
        if request.system:
            params["system"] = request.system
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def generate(self, request: LLMRequest) -> LLMResponse:
        client = self._get_client()
        params = self._params(request)
        create = client.beta.messages.create if self.fallbacks else client.messages.create
        try:
            msg = create(**params)
        except anthropic.APITimeoutError:
            raise self._fail("timeout", "request timed out", retryable=True) from None
        except anthropic.APIConnectionError:
            raise self._fail("connection", "connection failed", retryable=True) from None
        except anthropic.RateLimitError:
            raise self._fail("rate_limited", "HTTP 429", retryable=True) from None
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise self._fail("auth", f"HTTP {exc.status_code}", retryable=False) from None
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 408:
                kind = "timeout" if exc.status_code == 408 else "server_error"
                raise self._fail(kind, f"HTTP {exc.status_code}",
                                 retryable=True) from None  # fmt: skip
            raise self._fail("bad_request", f"HTTP {exc.status_code}", retryable=False) from None
        except anthropic.AnthropicError as exc:
            raise self._fail("client_error", type(exc).__name__, retryable=False) from None
        return self._to_response(msg)

    def _to_response(self, msg: Any) -> LLMResponse:
        stop = getattr(msg, "stop_reason", None)
        try:
            usage = msg.usage
            billed = (int(usage.input_tokens or 0), int(usage.output_tokens or 0))
            text = "".join(
                b.text for b in msg.content if getattr(b, "type", None) == "text"
            ).strip()
        except (AttributeError, TypeError, ValueError):
            raise self._fail("bad_response", "malformed message payload",
                             retryable=False, usage=(0, 0)) from None  # fmt: skip
        if stop == "refusal":
            details = getattr(msg, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise self._fail("refusal", f"model declined (category={category})",
                             retryable=False, usage=billed)  # fmt: skip
        # empty / truncated text is detected (and billed) by the gateway
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(getattr(msg, "model", None) or self.model),
            input_tokens=billed[0],
            output_tokens=billed[1],
            stop_reason=stop,
        )
