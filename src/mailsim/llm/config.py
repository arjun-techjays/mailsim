"""LLM gateway configuration (environment-driven) and secret redaction.

Environment variables (all optional; the default provider is the offline `stub`):

- ``MAILSIM_LLM_PROVIDER``: ``stub`` | ``anthropic`` | ``openai_compatible`` (default ``stub``).
- ``MAILSIM_LLM_MODEL``: model id. Defaults: anthropic -> ``claude-sonnet-5-5`` (set
  ``claude-opus-5-5`` for Opus); stub -> ``stub-1``; openai_compatible -> required.
- ``MAILSIM_LLM_BASE_URL``: OpenAI-compatible base URL, e.g. ``http://127.0.0.1:11434/v1``
  (Ollama). ``MAILSIM_OLLAMA_URL`` is accepted as a fallback. Must be ``https://`` unless
  the host is loopback.
- ``MAILSIM_LLM_API_KEY``: optional bearer key for the OpenAI-compatible endpoint.
- ``ANTHROPIC_API_KEY``: read by the Anthropic SDK itself; never stored in this config.
  ``ANTHROPIC_BASE_URL`` (also read by the SDK) may redirect Claude traffic, e.g. to a
  gateway; in deployment it must still be reachable only through the egress proxy.
- ``MAILSIM_LLM_TIMEOUT_SECONDS`` (default 60, float), ``MAILSIM_LLM_MAX_RETRIES`` (default 2).
- ``MAILSIM_LLM_EFFORT``: optional Anthropic ``output_config.effort`` (low..max).
- ``MAILSIM_LLM_FALLBACKS``: Anthropic server-side refusal fallbacks (default on;
  ``0``/``false``/``no``/``off`` disables).
- ``MAILSIM_LLM_STUB_FIXTURES``: path to a stub fixture table (JSON).
- ``MAILSIM_LLM_MAX_CALLS`` / ``MAILSIM_LLM_MAX_INPUT_TOKENS`` / ``MAILSIM_LLM_MAX_OUTPUT_TOKENS``:
  per-run budget overrides.

Egress: in deployment (``MAILSIM_ENV=deployment``) a non-stub provider refuses to start
unless ``HTTPS_PROXY`` points at the allowlist egress proxy; the Anthropic SDK and the
OpenAI-compatible client both honour it, and the ``app`` service sits on an internal network.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mailsim.domain.errors import InvalidRequest
from mailsim.llm.contract import DEFAULT_MODEL, Budget, ProviderName

OPUS_MODEL = "claude-opus-5-5"
STUB_MODEL = "stub-1"
SECRET_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "MAILSIM_LLM_API_KEY",
    "OPENAI_API_KEY",
)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_PROVIDERS: tuple[ProviderName, ...] = ("stub", "anthropic", "openai_compatible")
_EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
_FALSY = frozenset({"0", "false", "no", "off"})
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{6,}"),
    re.compile(r"(?i)(x-api-key|api[_-]?key|authorization)(\"?\s*[:=]\s*\"?)[^\s\"',}]+"),
)
REDACTED = "[REDACTED]"


def check_base_url(url: str) -> str:
    """Only https, or plain http to a loopback host (local Ollama)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return url
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return url
    raise ValueError("base_url must be https:// (plain http only for a loopback host)")


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName = "stub"
    model: str = STUB_MODEL
    base_url: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)
    effort: Effort | None = None
    fallbacks: bool = True
    stub_fixtures: str | None = None
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def _url(self) -> LLMConfig:
        if self.base_url is not None:
            check_base_url(self.base_url)
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        e = os.environ if env is None else env
        provider = e.get("MAILSIM_LLM_PROVIDER", "stub").strip() or "stub"
        if provider not in _PROVIDERS:
            raise InvalidRequest(
                f"MAILSIM_LLM_PROVIDER must be one of {list(_PROVIDERS)}",
                details={"provider": provider},
            )
        if (
            provider != "stub"
            and e.get("MAILSIM_ENV", "").strip().lower() == "deployment"
            and not (e.get("HTTPS_PROXY") or e.get("https_proxy"))
        ):
            raise InvalidRequest(
                "MAILSIM_ENV=deployment requires HTTPS_PROXY (allowlist egress proxy) "
                "for a non-stub LLM provider"
            )
        base_url = e.get("MAILSIM_LLM_BASE_URL") or e.get("MAILSIM_OLLAMA_URL") or None
        model = e.get("MAILSIM_LLM_MODEL", "").strip()
        if not model:
            if provider == "openai_compatible":
                raise InvalidRequest("MAILSIM_LLM_MODEL is required for openai_compatible")
            model = DEFAULT_MODEL if provider == "anthropic" else STUB_MODEL
        if provider == "openai_compatible" and not base_url:
            raise InvalidRequest("MAILSIM_LLM_BASE_URL is required for openai_compatible")
        effort = e.get("MAILSIM_LLM_EFFORT", "").strip() or None
        if effort is not None and effort not in _EFFORTS:
            raise InvalidRequest(f"MAILSIM_LLM_EFFORT must be one of {list(_EFFORTS)}")
        default = Budget()
        try:
            budget = Budget(
                max_calls=_int(e, "MAILSIM_LLM_MAX_CALLS", default.max_calls),
                max_input_tokens=_int(e, "MAILSIM_LLM_MAX_INPUT_TOKENS", default.max_input_tokens),
                max_output_tokens=_int(
                    e, "MAILSIM_LLM_MAX_OUTPUT_TOKENS", default.max_output_tokens
                ),
            )
            return cls(
                provider=provider,
                model=model,
                base_url=base_url,
                timeout_seconds=_float(e, "MAILSIM_LLM_TIMEOUT_SECONDS", 60.0),
                max_retries=_int(e, "MAILSIM_LLM_MAX_RETRIES", 2),
                effort=effort,
                fallbacks=e.get("MAILSIM_LLM_FALLBACKS", "1").strip().lower() not in _FALSY,
                stub_fixtures=e.get("MAILSIM_LLM_STUB_FIXTURES") or None,
                budget=budget,
            )
        except ValidationError as exc:
            fields = sorted({".".join(str(x) for x in err["loc"]) or "config"
                             for err in exc.errors()})  # fmt: skip
            raise InvalidRequest("invalid LLM configuration", details={"fields": fields}) from None


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise InvalidRequest(f"{name} must be an integer") from None


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise InvalidRequest(f"{name} must be a number") from None


def secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """Currently configured secret values (only non-trivial ones, longest first)."""
    e = os.environ if env is None else env
    vals = {e[k] for k in SECRET_ENV_VARS if len(e.get(k, "")) >= 8}
    return sorted(vals, key=len, reverse=True)


def redact(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Remove API keys / bearer tokens from text destined for errors or logs."""
    out = text
    for secret in [*secret_values(), *extra_secrets]:
        if secret:
            out = out.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}" if m.re.groups == 2 else REDACTED,
            out,
        )
    return out
