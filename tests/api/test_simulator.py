"""Simulator control: inject, clock, scheduler, state, actions."""

from __future__ import annotations

import base64

import pytest

from tests.api.conftest import KEY
from tests.helpers import PLAIN

pytestmark = pytest.mark.db


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def run(api):
    api.publish()
    created = api.create_run()
    key, mbx = api.agent(created)
    return created["run"]["id"], mbx, key


def test_inject_dict_and_raw(api, run):
    rid, mbx, key = run
    out = api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", expect=201, json={
        "event_id": "evt_104",
        "message": {"from": "vendor@example.test", "to": ["finance@example.test"],
                    "cc": ["procurement@example.test"], "subject": "Updated bank details",
                    "text": "Please use our new bank account."}}).json()  # fmt: skip
    assert out["event_id"] == "evt_104"
    assert out["delivery_status"] == "delivered_to_simulated_mailbox"
    raw = api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", expect=201,
                   json={"raw_base64": base64.b64encode(PLAIN.replace(b"invoice-001", b"inj-2")).decode()}).json()  # fmt: skip
    assert raw["event_id"].startswith("evt_")
    msg = api.call("GET", f"/v1/mailboxes/{mbx}/messages/{out['message_id']}", key=key,
                   expect=200).json()  # fmt: skip
    assert (
        msg["subject"] == "Updated bank details" and msg["from"]["email"] == "vendor@example.test"
    )
    api.call("GET", f"/v1/mailboxes/{mbx}/messages/{raw['message_id']}", key=key, expect=200)


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({}, 400, "INVALID_REQUEST"),
        ({"raw_base64": "eA==", "message": {"from": "a@example.test", "to": ["b@example.test"]}},
         400, "INVALID_REQUEST"),
        ({"raw_base64": "@@@"}, 400, "INVALID_REQUEST"),
        ({"message": {"from": "a@example.test", "to": []}}, 400, "INVALID_REQUEST"),
        ({"message": {"from": "not an address", "to": ["b@example.test"]}}, 400, "INVALID_REQUEST"),
        ({"raw_base64": "eA==", "delivery": {"mode": "scheduled"}}, 501, "UNSUPPORTED_OPERATION"),
    ],
)  # fmt: skip
def test_inject_errors(api, run, body, status, code):
    rid, mbx, _ = run
    err(api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", json=body), status, code)


def test_inject_into_unknown_mailbox_or_inactive_run(api, run):
    rid, mbx, _ = run
    msg = {"message": {"from": "a@example.test", "to": ["finance@example.test"]}}
    err(api.call("POST", f"/v1/runs/{rid}/mailboxes/mbx_nope/inject", json=msg), 404, "NOT_FOUND")
    api.call("POST", f"/v1/runs/{rid}/cancel", expect=200)
    err(api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", json=msg), 409,
        "RUN_NOT_ACTIVE")  # fmt: skip


def test_clock_advance_tick_and_limits(api, run):
    rid, *_ = run
    fired = api.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 700},
                     expect=200).json()  # fmt: skip
    assert (
        fired["fired"] == ["vendor-follow-up"] and fired["virtual_time"] == "2026-10-15T09:11:40Z"
    )
    tick = api.call("POST", f"/v1/runs/{rid}/scheduler/tick", expect=200).json()
    assert tick == {"fired": [], "virtual_time": "2026-10-15T09:11:40Z", "persona_replies": 0}
    err(api.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": -1}), 400,
        "INVALID_REQUEST")  # fmt: skip
    body = err(api.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 99999}), 429,
               "SIMULATION_LIMIT_EXCEEDED")  # fmt: skip
    assert body["details"] == {"limit": 3600}


def test_state_and_actions_by_role(api, run):
    rid, mbx, key = run
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key, expect=200)
    ctl = api.call("GET", f"/v1/runs/{rid}/state", expect=200).json()
    ev = api.call("GET", f"/v1/runs/{rid}/state", "evaluator", expect=200).json()
    assert ctl == ev and {"run", "messages", "mailboxes"} <= set(ctl)
    assert "answer_key" not in str(ctl) and "Agent must not claim" not in str(ctl)
    body = err(api.call("GET", f"/v1/runs/{rid}/state?at_sequence=3"), 403, "PERMISSION_DENIED")
    assert "evaluator" in body["message"]
    early = api.call("GET", f"/v1/runs/{rid}/state?at_sequence=1", "evaluator", expect=200).json()
    assert early["messages"] == [] and early["run"]["status"] == "DRAFT"
    err(api.call("GET", f"/v1/runs/{rid}/state?at_sequence=99999", "evaluator"), 400,
        "INVALID_REQUEST")  # fmt: skip
    acts = api.call("GET", f"/v1/runs/{rid}/actions", "evaluator", expect=200).json()
    assert ("agent", "read", "ok") in {(a["actor"], a["operation"], a["outcome"]) for a in acts}
    assert api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json() == acts
    assert KEY  # scenario key used by the fixture
