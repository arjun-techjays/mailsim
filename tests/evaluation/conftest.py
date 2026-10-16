"""Fixtures for evaluator tests: a running example run with an open agent and key helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.evaluation.contract import AssertionOutcome, EvaluationReport
from mailsim.evaluation.engine import evaluate_run
from mailsim.evaluation.service import EvaluationService
from mailsim.scenario.answer_key import AnswerKeyDoc
from mailsim.scenario.answer_keys import _scenario, _store, create_answer_key_version
from tests.scenarios import write_variant

KEY = "finance.invoice-bank-change"
AGENT_EMAIL = "finance@example.test"
VENDOR_EMAIL = "vendor@example.test"


def open_agent(s: dict[str, Any]) -> None:
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 50


class FakeJudge:
    """Records exactly what the judge was given; answers from a fixed result."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else {
            "passed": True, "score": 0.9, "explanation": "fine", "llm_call_id": "llm_1",
            "model": "stub-judge", "provider": "stub", "input_tokens": 11, "output_tokens": 3,
        }  # fmt: skip
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def judge(self, *, requirement: str, rubric: str, agent_texts: list[str]) -> Any:
        self.calls.append({"requirement": requirement, "rubric": rubric, "texts": agent_texts})
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class World:
    env: Any
    ctl: Controller
    view: EvaluatorView
    run: str
    agent: AgentMailbox
    fm: str
    vendor: str
    version_id: str
    next_key: list[int] = field(default_factory=lambda: [2])

    def key(self, *assertions: dict[str, Any], checks_default: str = "on",
            scenario_versions: list[int] | None = None, unchecked: bool = False) -> int:  # fmt: skip
        """Publish an answer key; `unchecked` stores it without the publish-time reference
        checks (like a key stored before they existed), to test evaluation-time behaviour."""
        version = self.next_key[0]
        self.next_key[0] += 1
        doc = AnswerKeyDoc.model_validate({"answer_key_version": "1.0", "answer_key": {
            "scenario": KEY, "version": version, "checks_default": checks_default,
            "scenario_versions": scenario_versions, "assertions": list(assertions)}})  # fmt: skip
        with self.env.session_factory.begin() as s:
            if unchecked:
                _store(s, _scenario(s, self.env.scope, KEY), doc, "file")
            else:
                create_answer_key_version(s, self.env.scope, doc, self.env.blobs)
        return version

    def evaluate(self, version: int | None = None, *, checks: str | None = None,
                 judge: Any = None, run: str | None = None) -> EvaluationReport:  # fmt: skip
        return evaluate_run(self.view, run or self.run, answer_key_version=version,
                            checks=checks, judge=judge, evaluation_id="evl_test")  # fmt: skip

    def check(self, assertion: dict[str, Any], *, checks: str | None = None,
              judge: Any = None, unchecked: bool = False) -> AssertionOutcome:  # fmt: skip
        report = self.evaluate(self.key(assertion, unchecked=unchecked), checks=checks,
                               judge=judge)  # fmt: skip
        assert len(report.outcomes) == 1
        return report.outcomes[0]

    def new_run(self, *, agent_build: str | None = None, checks: str | None = None) -> str:
        run = self.ctl.create_run(self.version_id, agent_build=agent_build, checks=checks)
        self.ctl.start_run(run)
        return run

    def agent_for(self, run: str) -> AgentMailbox:
        return AgentMailbox(self.env.engine, self.env.scope, run,
                            self.ctl.mailbox_id_for(run, "finance_manager"))  # fmt: skip

    def service(self, **kw: Any) -> EvaluationService:
        return EvaluationService(self.env.engine, self.env.session_factory, self.env.blobs, **kw)

    def inbox_id(self, subject: str, run: str | None = None) -> str:
        agent = self.agent_for(run) if run else self.agent
        for m in agent.list_messages(folder="inbox").items:
            if m.subject == subject:
                return m.id
        raise AssertionError(f"no inbox message {subject!r}")


def build_world(env: Any, tmp_path: Path, mutate: Any = open_agent, *,
                checks: str | None = None, agent_build: str | None = "b1") -> World:  # fmt: skip
    ctl = Controller(env.engine, env.scope)
    version_id = env.version(write_variant(tmp_path, mutate))
    run = ctl.create_run(version_id, agent_build=agent_build, checks=checks)
    ctl.start_run(run)
    fm = ctl.mailbox_id_for(run, "finance_manager")
    return World(env, ctl, EvaluatorView(env.engine, env.scope), run,
                 AgentMailbox(env.engine, env.scope, run, fm), fm,
                 ctl.mailbox_id_for(run, "vendor"), version_id)  # fmt: skip


@pytest.fixture()
def world(env: Any, tmp_path: Path) -> World:
    return build_world(env, tmp_path)
