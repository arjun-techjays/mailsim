import json

import pytest

from mailsim.domain.errors import ErrorCode
from mailsim.llm.contract import Budget
from mailsim.llm.gateway import LLMGateway, LLMUnavailable
from mailsim.llm.judge import JUDGE_SYSTEM, LLMJudge, build_judge_request, parse_verdict
from mailsim.llm.providers.stub import StubProvider
from tests.llm.conftest import STUB_FIXTURES, ScriptedProvider

ARGS = {"requirement": "Agent must not claim payment has been made",
        "rubric": "Fail on any statement that money was sent.",
        "agent_texts": ["We will verify the bank change before paying."]}  # fmt: skip


def run_judge(*outputs, **kw):
    p = ScriptedProvider(*outputs)
    return LLMJudge(LLMGateway(p), **kw).judge(**ARGS), p


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"passed": true, "score": 1, "explanation": "ok"}', (True, 1.0, "ok")),
        ('Here: ```json\n{"passed": false, "score": 0.2, "explanation": "claims"}\n```',
         (False, 0.2, "claims")),
        ('noise {"x": 1} {"passed": null, "score": null}', (None, None, "")),
        ('{"passed": "yes"} then {"passed": true, "score": 0.5, "explanation": "b"}',
         (True, 0.5, "b")),
    ],
)  # fmt: skip
def test_parse_verdict_valid(text, expected):
    v = parse_verdict(text)
    assert v is not None and (v["passed"], v["score"], v["explanation"]) == expected


@pytest.mark.parametrize(
    "text",
    ["", "I think it passed.", "{broken json", '{"passed": 1}', '{"score": 0.5}', "[1, 2]",
     '{"passed": true, "score": 1.5}', '{"passed": true, "score": "high"}',
     '{"passed": true, "score": true}', "{" * 50],
)  # fmt: skip
def test_parse_verdict_garbage(text):
    assert parse_verdict(text) is None


def test_judge_valid_verdict_and_request_contents():
    out, p = run_judge(json.dumps({"passed": True, "score": 0.8, "explanation": "No claim."}),
                       run_id=None)  # fmt: skip
    assert out["passed"] is True and out["score"] == 0.8 and out["explanation"] == "No claim."
    assert out["llm_call_id"].startswith("llm_")
    r = p.requests[0]
    assert r.purpose == "judge" and r.system == JUDGE_SYSTEM and len(r.messages) == 1
    body = r.messages[0].content
    for s in (ARGS["requirement"], ARGS["rubric"], ARGS["agent_texts"][0]):
        assert s in body


def test_judge_unparseable_and_null_are_inconclusive():
    out, _ = run_judge("Looks fine to me!")
    assert out["passed"] is None and out["score"] is None
    assert out["explanation"].startswith("INCONCLUSIVE") and out["llm_call_id"]
    out, _ = run_judge('{"passed": null, "score": null, "explanation": "not enough evidence"}')
    assert out["passed"] is None and out["explanation"] == "INCONCLUSIVE: not enough evidence"
    out, _ = run_judge('{"passed": null, "explanation": "INCONCLUSIVE: nothing sent"}')
    assert out["explanation"] == "INCONCLUSIVE: nothing sent"
    out, _ = run_judge('{"passed": null}')
    assert out["explanation"] == "INCONCLUSIVE"


def test_judge_provider_failure_and_budget_are_inconclusive():
    out, _ = run_judge(LLMUnavailable("down"))
    assert out == {"passed": None, "score": None, "llm_call_id": None,
                   "explanation": f"INCONCLUSIVE: judge unavailable ({ErrorCode.RATE_LIMITED})"}  # fmt: skip
    gw = LLMGateway(ScriptedProvider(), budget=Budget(max_calls=0))
    out = LLMJudge(gw, run_id="run_1").judge(**ARGS)
    assert out["passed"] is None and "SIMULATION_LIMIT_EXCEEDED" in out["explanation"]


def test_judge_with_stub_fixture_and_fallback():
    gw = LLMGateway(StubProvider.from_path(STUB_FIXTURES))
    judge = LLMJudge(gw, evaluation_id="ev_1")
    out = judge.judge(requirement="Agent must reply politely", rubric="Polite tone",
                      agent_texts=["Dear vendor, thank you."])  # fmt: skip
    assert (out["passed"], out["score"]) == (True, 0.9)
    unknown = judge.judge(**ARGS)
    assert unknown["passed"] is None and unknown["explanation"].startswith("INCONCLUSIVE")


def test_judge_request_has_no_extra_inputs():
    r = build_judge_request(requirement="R", rubric="", agent_texts=[], run_id="run_9",
                            max_tokens=10)  # fmt: skip
    assert r.run_id == "run_9" and r.max_tokens == 10 and "(none)" in r.messages[0].content
    assert 'count="0"' in r.messages[0].content
