"""Idempotency-Key: authorize first, replay same request, CONFLICT on reuse, 24h expiry."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from mailsim.api.idempotency import REDACTED, REPLAYED_HEADER
from mailsim.store import models as m
from tests.api.conftest import KEY, scenario_payload

pytestmark = pytest.mark.db


def runs_in(api) -> int:
    with api.sf() as s:
        return s.scalar(
            select(func.count()).select_from(m.Run).where(m.Run.tenant_id == api.tenant_id)
        )


def test_run_create_replays_and_redacts_the_secret(api):
    api.publish()
    hdr = {"Idempotency-Key": "create-1"}
    first = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    again = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    assert REPLAYED_HEADER not in first.headers and again.headers[REPLAYED_HEADER] == "true"
    assert again.json()["run"] == first.json()["run"] and runs_in(api) == 1
    assert first.json()["agent_credentials"][0]["api_key"].startswith("msk_")
    assert again.json()["agent_credentials"][0]["api_key"] == REDACTED
    with api.sf() as s:  # the plaintext key never reaches the idempotency table
        stored = s.scalars(select(m.IdempotencyKey.response)).all()
    assert first.json()["agent_credentials"][0]["api_key"] not in str(stored)
    # a different request under the same key is a CONFLICT; another key creates a new run
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={"start": False}, headers=hdr)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers={"Idempotency-Key": "c2"},
             expect=201)  # fmt: skip
    assert runs_in(api) == 2


def test_keys_are_scoped_to_the_credential(api):
    api.publish()
    created = api.call("POST", "/v1/projects", json={"name": "p2"}, expect=201).json()
    hdr = {"Idempotency-Key": "shared"}
    api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    # the same Idempotency-Key from a different credential is an independent request
    other_ctl = next(c["api_key"] for c in created["credentials"] if c["role"] == "controller")
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, key=other_ctl)
    assert r.status_code == 404 and REPLAYED_HEADER not in r.headers  # no scenario in project 2


def test_authorization_happens_before_lookup(api):
    api.publish()
    hdr = {"Idempotency-Key": "auth-first"}
    created = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    rid = created.json()["run"]["id"]
    agent_key = created.json()["agent_credentials"][0]["api_key"]
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, role="evaluator")
    assert r.status_code == 403
    r = api.call("POST", f"/v1/runs/{rid}/cancel", headers=hdr, key=agent_key)
    assert r.status_code == 403
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, role=None)
    assert r.status_code == 401


def test_failures_are_not_stored_and_expired_keys_rerun(api):
    hdr = {"Idempotency-Key": "retry"}
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr)
    assert r.status_code == 404
    api.publish()
    first = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    with api.sf.begin() as s:
        s.execute(update(m.IdempotencyKey).where(m.IdempotencyKey.tenant_id == api.tenant_id)
                  .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))  # fmt: skip
    again = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers=hdr, expect=201)
    assert again.json()["run"]["id"] != first.json()["run"]["id"]
    assert REPLAYED_HEADER not in again.headers
    with api.sf() as s:
        row = s.scalars(
            select(m.IdempotencyKey).where(m.IdempotencyKey.tenant_id == api.tenant_id)
        ).one()
        assert row.expires_at > datetime.now(UTC) + timedelta(hours=23)
        assert row.method_path == f"POST /v1/scenarios/{KEY}/runs" and row.status_code == 201
    r = api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}, headers={"Idempotency-Key": " "})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"


def test_concurrent_duplicates_run_once(api):
    api.publish()
    results = []

    def go():
        results.append(api.call("POST", f"/v1/scenarios/{KEY}/runs", json={},
                                headers={"Idempotency-Key": "race"}))  # fmt: skip

    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # exactly one executes; the others either replay it or see it still in progress (409)
    assert runs_in(api) == 1
    fresh = [r for r in results if r.status_code == 201 and REPLAYED_HEADER not in r.headers]
    assert len(fresh) == 1
    for r in results:
        assert r.status_code in (201, 409)
        if r.status_code == 201:
            assert r.json()["run"]["id"] == fresh[0].json()["run"]["id"]
        else:
            assert "in progress" in r.json()["error"]["message"]


def test_every_idempotent_operation_replays(api):
    v = api.call("POST", "/v1/scenarios", json=scenario_payload(), headers={"Idempotency-Key": "pub"},
                 expect=201)  # fmt: skip
    again = api.call("POST", "/v1/scenarios", json=scenario_payload(),
                     headers={"Idempotency-Key": "pub"}, expect=201)  # fmt: skip
    assert again.json() == v.json() and again.headers[REPLAYED_HEADER] == "true"
    created = api.create_run()
    rid, (_, mbx) = created["run"]["id"], api.agent(created)
    msg = {"message": {"from": "vendor@example.test", "to": ["finance@example.test"], "text": "x"}}
    calls = [
        ("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", msg, 201, "controller"),
        ("POST", f"/v1/runs/{rid}/clock/advance", {"seconds": 5}, 200, "controller"),
        ("POST", f"/v1/runs/{rid}/replay", None, 200, "controller"),
        ("POST", f"/v1/runs/{rid}/evaluations", {"checks": "on"}, 201, "evaluator"),
        ("POST", f"/v1/runs/{rid}/reset", None, 200, "controller"),
        ("POST", f"/v1/runs/{rid}/cancel", None, 200, "controller"),
    ]
    for i, (method, url, body, status, role) in enumerate(calls):
        kw = {"json": body} if body is not None else {}
        hdr = {"Idempotency-Key": f"op-{i}"}
        a = api.call(method, url, role, headers=hdr, expect=status, **kw)
        b = api.call(method, url, role, headers=hdr, expect=status, **kw)
        assert b.json() == a.json() and b.headers[REPLAYED_HEADER] == "true", url
    assert [c[0] for c in api.evaluations.calls].count("evaluate") == 1
    events = api.call("GET", f"/v1/runs/{rid}/events?page_size=500", "evaluator").json()["items"]
    assert [e["type"] for e in events].count("run.reset") == 1
