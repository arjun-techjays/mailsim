"""W2.0: agent keys end with their run; terminal runs get no new agent keys; sweeping."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from mailsim import serve as server
from mailsim.api.idempotency import sweep_expired
from mailsim.engine.facades import Controller
from mailsim.store import models as m
from mailsim.store.repos import Scope

pytestmark = pytest.mark.db


def test_agent_key_dies_with_its_run_even_on_error(api):
    api.publish()
    created = api.create_run()
    rid = created["run"]["id"]
    key, mbx = api.agent(created)
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key, expect=200)
    Controller(api.engine, Scope(api.tenant_id, api.project_id)).fail_run_with_error(rid, "boom")
    r = api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = api.call("POST", f"/v1/runs/{rid}/agent-credentials")
    assert r.status_code == 409 and r.json()["error"]["details"] == {"status": "ERROR"}


def test_sweep_removes_only_expired_records(api, db_engine, monkeypatch, capsys):
    api.publish()
    with api.sf.begin() as s:
        old = datetime.now(UTC) - timedelta(days=2)
        for i in range(3):
            s.add(m.IdempotencyKey(tenant_id=api.tenant_id, project_id=api.project_id, key=f"k{i}",
                                   key_id="key_x", method_path="POST /x", request_hash="h",
                                   status_code=200, response={},
                                   expires_at=old if i < 2 else datetime.now(UTC) + timedelta(hours=1)))  # fmt: skip
    with api.sf.begin() as s:
        assert sweep_expired(s, datetime.now(UTC), limit=1) == 1
    url = db_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("MAILSIM_DATABASE_URL", url)
    assert server.main(["sweep"]) == 0
    assert json.loads(capsys.readouterr().out)["swept"] >= 1
    with api.sf() as s:
        left = s.scalar(select(func.count()).select_from(m.IdempotencyKey)
                        .where(m.IdempotencyKey.key_id == "key_x"))  # fmt: skip
    assert left == 1


def test_claims_sweep_their_own_project(api):
    api.publish()
    with api.sf.begin() as s:
        s.add(m.IdempotencyKey(tenant_id=api.tenant_id, project_id=api.project_id, key="stale",
                               key_id="key_y", method_path="POST /x", request_hash="h",
                               status_code=200, response={},
                               expires_at=datetime.now(UTC) - timedelta(days=1)))  # fmt: skip
    api.call("POST", "/v1/scenarios/finance.invoice-bank-change/runs",
             headers={"Idempotency-Key": "fresh"}, json={}, expect=201)  # fmt: skip
    with api.sf() as s:
        assert s.get(m.IdempotencyKey, (api.tenant_id, api.project_id, "stale", "key_y")) is None
