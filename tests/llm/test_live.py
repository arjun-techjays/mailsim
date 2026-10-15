"""Live smoke tests: real model calls. Skipped unless credentials/endpoints are configured.

Run: ANTHROPIC_API_KEY=... pytest -m live_llm tests/llm/test_live.py
     MAILSIM_OLLAMA_URL=http://127.0.0.1:11434/v1 MAILSIM_LLM_MODEL=llama3.1 pytest -m live_llm ...
"""

import os

import pytest

from mailsim.llm.config import LLMConfig
from mailsim.llm.contract import LLMRequest, Message
from mailsim.llm.gateway import LLMGateway

pytestmark = pytest.mark.live_llm
REQ = LLMRequest(purpose="generation", system="Answer with one word.",
                 messages=[Message(role="user", content="Say: pong")], max_tokens=2048)  # fmt: skip


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_live_anthropic():
    model = os.environ.get("MAILSIM_LLM_MODEL") or "claude-sonnet-5-5"
    gw = LLMGateway.from_config(LLMConfig(provider="anthropic", model=model))
    r = gw.generate(REQ)
    assert r.text and r.provider == "anthropic" and r.output_tokens > 0


@pytest.mark.skipif(not os.environ.get("MAILSIM_OLLAMA_URL"), reason="MAILSIM_OLLAMA_URL not set")
def test_live_ollama():
    cfg = LLMConfig(provider="openai_compatible", base_url=os.environ["MAILSIM_OLLAMA_URL"],
                    model=os.environ.get("MAILSIM_LLM_MODEL", "llama3.1"))  # fmt: skip
    assert LLMGateway.from_config(cfg).generate(REQ).text
