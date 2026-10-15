import json

import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.llm.contract import LLMRequest, Message
from mailsim.llm.gateway import prompt_hash
from mailsim.llm.providers.stub import StubProvider
from tests.llm.conftest import STUB_FIXTURES


def req(content="hello", purpose="generation", system="fixture-test"):
    return LLMRequest(purpose=purpose, system=system,
                      messages=[Message(role="user", content=content)])  # fmt: skip


def test_fixture_lookup_is_keyed_by_prompt_hash():
    stub = StubProvider.from_path(STUB_FIXTURES)
    r = stub.generate(req())
    assert r.text == "Hello from the stub fixture table." and r.stop_reason == "end_turn"
    assert r.provider == "stub" and r.model == "stub-1"
    # run_id / max_tokens do not change the key (budget capping keeps fixtures stable)
    other = req().model_copy(update={"run_id": "run_x", "max_tokens": 7})
    assert prompt_hash(other) == prompt_hash(req())
    assert stub.generate(other).text == r.text


def test_fallback_is_deterministic_with_deterministic_tokens():
    stub = StubProvider()
    a, b = stub.generate(req("x")), stub.generate(req("x"))
    assert a == b and a.text.startswith("Thank you for your email.")
    assert a.output_tokens == -(-len(a.text) // 4) and a.input_tokens >= 1
    assert stub.generate(req("y")).text != a.text
    verdict = json.loads(stub.generate(req(purpose="judge")).text)
    assert verdict["passed"] is None and verdict["score"] is None


def test_from_path_none_and_errors(tmp_path):
    assert StubProvider.from_path(None, model="m").model == "m"
    with pytest.raises(InvalidRequest, match="cannot load"):
        StubProvider.from_path(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(InvalidRequest, match="cannot load"):
        StubProvider.from_path(bad)
    for payload in ([], {"responses": []}, {"responses": {"k": {"text": 1}}}):
        bad.write_text(json.dumps(payload))
        with pytest.raises(InvalidRequest, match="must be"):
            StubProvider.from_path(bad)
