"""Integration: the persona runtime answers during clock steps; the judge is wired per run."""

from __future__ import annotations

import pytest

from mailsim import serve as server
from mailsim.llm.gateway import LLMGateway
from mailsim.llm.providers.stub import StubProvider
from mailsim.personas.runtime import PersonaRuntime
from tests.api.conftest import KEY, open_agent

pytestmark = pytest.mark.db


def scripted_vendor(s):
    open_agent(s)
    s["personas"][1]["behavior"] = {
        "mode": "scripted",
        "response_delay_seconds": 30,
        "script": [{"on": {"from": "finance_manager", "subject_contains": "Invoice"},
                    "reply": {"body": "Please pay to our NEW bank account."}}],
    }  # fmt: skip


def test_clock_steps_run_the_persona_runtime(api, tmp_path):
    gateway = LLMGateway(StubProvider())
    api.app.state.persona_runtime_factory = lambda ctl: PersonaRuntime(ctl, gateway)
    api.publish(scripted_vendor, tmp=tmp_path)
    created = api.create_run()
    rid = created["run"]["id"]
    key, mbx = api.agent(created)
    inbox = api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key, expect=200).json()["items"]
    invoice = next(m for m in inbox if m["subject"] == "Invoice INV-001")
    api.call("POST", f"/v1/mailboxes/{mbx}/messages/{invoice['id']}/reply", key=key, expect=201,
             json={"text": "We will verify the bank details first."})  # fmt: skip
    early = api.call("POST", f"/v1/runs/{rid}/scheduler/tick", expect=200).json()
    assert early["persona_replies"] == 0, "the vendor's response delay has not elapsed"
    later = api.call(
        "POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 30}, expect=200
    ).json()
    assert later["persona_replies"] == 1
    again = api.call(
        "POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 30}, expect=200
    ).json()
    assert again["persona_replies"] == 0, "answered once"
    inbox = api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key, expect=200).json()["items"]
    assert any("NEW bank account" in (m["body_text"] or "") for m in inbox)
    assert api.call("POST", f"/v1/runs/{rid}/replay", expect=200).json()["matches"] is True
    _ = KEY


def test_without_a_runtime_personas_stay_silent(api, tmp_path):
    api.publish()
    rid = api.create_run()["run"]["id"]
    assert (
        api.call("POST", f"/v1/runs/{rid}/scheduler/tick", expect=200).json()["persona_replies"]
        == 0
    )


def test_build_state_wires_gateway_runtime_and_judge(db_engine, tmp_path, monkeypatch):
    url = db_engine.url.render_as_string(hide_password=False)
    monkeypatch.delenv("MAILSIM_LLM_PROVIDER", raising=False)
    state = server.build_state({"MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(tmp_path)})
    try:
        assert state["llm_gateway"].provider.name == "stub"
        runtime = state["persona_runtime_factory"](object())
        assert isinstance(runtime, PersonaRuntime)
        judge = state["evaluation_service"]._judge_factory("run_x")
        assert judge.run_id == "run_x"
    finally:
        state["db_engine"].dispose()
