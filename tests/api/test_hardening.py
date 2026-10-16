"""Review fixes: size limits, key revocation, partial PATCH, two-phase idempotency."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mailsim.api import limits
from mailsim.api import schemas as s
from mailsim.api.idempotency import REPLAYED_HEADER
from mailsim.store import models as m
from mailsim.store.repos import Scope
from tests.api.conftest import KEY, Api, FakeEvaluationService, open_agent, scenario_payload

pytestmark = pytest.mark.db


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


# ---- request size limits ---------------------------------------------------------------
def test_body_limit_by_content_length_and_by_stream(api, monkeypatch):
    api.app.state.max_body_bytes = 64
    body = err(api.call("POST", "/v1/projects", content=b"x" * 65,
                        headers={"content-type": "application/json"}), 400, "INVALID_REQUEST")  # fmt: skip
    assert body["message"] == limits.TOO_LARGE and body["details"] == {"limit_bytes": 64}

    def chunks():
        yield b'{"name": "'
        yield b"y" * 100
        yield b'"}'

    body = err(api.call("POST", "/v1/projects", content=chunks(),
                        headers={"content-type": "application/json"}), 400, "INVALID_REQUEST")  # fmt: skip
    assert body["message"] == limits.TOO_LARGE
    err(api.call("POST", "/v1/projects", content=b"{}", headers={"content-length": "abc"}), 400,
        "INVALID_REQUEST")  # fmt: skip
    # small streamed bodies still reach the handler intact
    api.call("POST", "/v1/projects", content=(c for c in (b'{"name"', b': "ok"}')),
             headers={"content-type": "application/json"}, expect=201)  # fmt: skip
    api.app.state.max_body_bytes = None
    monkeypatch.setenv("MAILSIM_MAX_BODY_BYTES", "10")
    assert limits.max_body_bytes(api.app) == 10
    monkeypatch.delenv("MAILSIM_MAX_BODY_BYTES")
    assert limits.max_body_bytes(None) == limits.DEFAULT_MAX_BODY_BYTES


def test_body_limit_passes_through_other_scopes_and_disconnects():
    import asyncio

    asyncio.run(_passthrough())


async def _passthrough():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["type"], await receive()))

    mw = limits.BodySizeLimit(app)

    async def receive():
        return {"type": "http.disconnect"}

    await mw({"type": "lifespan"}, receive, None)
    await mw({"type": "http", "headers": []}, receive, None)
    assert seen[0] == ("lifespan", {"type": "http.disconnect"})
    assert seen[1] == ("http", {"type": "http.request", "body": b"", "more_body": False})

    msgs = [{"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
            {"type": "http.disconnect"}]  # fmt: skip

    async def chunked():
        return msgs.pop(0)

    async def reads_twice(scope, receive, send):
        seen.append((await receive(), await receive()))

    await limits.BodySizeLimit(reads_twice)({"type": "http", "headers": []}, chunked, None)
    assert seen[2] == ({"type": "http.request", "body": b"abcd", "more_body": False},
                       {"type": "http.disconnect"})  # fmt: skip


def test_schema_caps_on_source_and_base64(api):
    huge = "a" * (s.MAX_SOURCE_CHARS + 1)
    err(api.call("POST", "/v1/scenarios", json={"source": huge}), 400, "INVALID_REQUEST")
    err(api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": huge}), 400,
        "INVALID_REQUEST")  # fmt: skip
    big = "A" * (s.MAX_FIXTURE_B64_CHARS + 4)
    err(api.call("POST", "/v1/scenarios", json={"source": "x", "fixtures": {"f.eml": big}}),
        400, "INVALID_REQUEST")  # fmt: skip
    err(api.call("POST", "/v1/runs/run_x/mailboxes/mbx_x/inject",
                 json={"raw_base64": "A" * (s.MAX_RAW_B64_CHARS + 4)}), 400, "INVALID_REQUEST")  # fmt: skip


def test_fixture_byte_caps(api, monkeypatch):
    payload = scenario_payload()
    monkeypatch.setattr(s, "MAX_FIXTURE_B64_CHARS", 10)  # pre-decode length check
    body = err(api.call("POST", "/v1/scenarios", json=payload), 400, "INVALID_REQUEST")
    assert "too large" in body["message"]
    monkeypatch.undo()
    monkeypatch.setattr(
        "mailsim.api.authoring.MAX_FIXTURE_TOTAL_BYTES", 600
    )  # each fits, the sum does not
    body = err(api.call("POST", "/v1/scenarios", json=payload), 400, "INVALID_REQUEST")
    assert "in total" in body["message"]


# ---- keys ------------------------------------------------------------------------------
def test_delete_key_is_project_scoped(api, other_api):
    with api.sf() as session:
        ev_id = session.scalar(select(m.ApiKey.id).where(m.ApiKey.tenant_id == api.tenant_id,
                                                         m.ApiKey.role == "evaluator"))  # fmt: skip
    err(other_api.call("DELETE", f"/v1/keys/{ev_id}"), 404, "NOT_FOUND")
    err(api.call("DELETE", f"/v1/keys/{ev_id}", "evaluator"), 403, "PERMISSION_DENIED")
    err(api.call("DELETE", "/v1/keys/key_missing"), 404, "NOT_FOUND")
    api.call("GET", "/v1/scenarios", "evaluator", expect=200)
    assert api.call("DELETE", f"/v1/keys/{ev_id}", expect=204).content == b""
    err(api.call("GET", "/v1/scenarios", "evaluator"), 401, "UNAUTHENTICATED")


def test_agent_keys_survive_mint_and_are_revoked_on_cancel_and_completion(api):
    api.publish()
    created = api.create_run()
    rid, (key1, mbx) = created["run"]["id"], api.agent(created)
    folders = f"/v1/mailboxes/{mbx}/folders"
    key2 = api.call("POST", f"/v1/runs/{rid}/agent-credentials", expect=201).json()["api_key"]
    api.call("GET", folders, key=key1, expect=200)  # minting another key revokes nothing
    api.call("GET", folders, key=key2, expect=200)
    api.call("POST", f"/v1/runs/{rid}/cancel", expect=200)
    err(api.call("GET", folders, key=key1), 401, "UNAUTHENTICATED")
    err(api.call("GET", folders, key=key2), 401, "UNAUTHENTICATED")

    created = api.create_run()
    rid, (key3, mbx) = created["run"]["id"], api.agent(created)
    api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={"complete_run": False},
             expect=201)  # fmt: skip
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key3, expect=200)  # still running

    scope = Scope(api.tenant_id, api.project_id)
    fake = api.evaluations

    def completing(scope_, run_id, **kw):
        latest = api.engine.events(scope, run_id)[-1]["sequence"]
        api.engine.complete_run(scope, run_id, "PASSED", evaluation_id="evl_x",
                                evaluated_through_sequence=latest)  # fmt: skip
        return FakeEvaluationService.evaluate(fake, scope_, run_id, **kw)

    fake.evaluate = completing
    api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={}, expect=201)
    err(api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key3), 401, "UNAUTHENTICATED")


# ---- PATCH ordering --------------------------------------------------------------------
def test_patch_validates_folder_first_and_documents_partial_application(api):
    api.publish(open_agent)
    created = api.create_run()
    rid, (key, mbx) = created["run"]["id"], api.agent(created)
    msg = api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key).json()["items"][0]
    url = f"/v1/mailboxes/{mbx}/messages/{msg['id']}"
    actions = lambda: api.call("GET", f"/v1/runs/{rid}/actions").json()  # noqa: E731
    before = len(actions())
    err(api.call("PATCH", url, key=key, json={"folder": "nope", "add_labels": ["x"]}), 404,
        "NOT_FOUND")  # fmt: skip
    writes = [a for a in actions()[before:] if a["operation"] in ("move", "label")]
    assert writes == [], "an unknown folder is rejected before any write"
    # a failing later step leaves the earlier steps applied (documented)
    err(api.call("PATCH", url, key=key, json={"folder": "archive", "add_flags": ["BOGUS"]}), 400,
        "INVALID_REQUEST")  # fmt: skip
    after = api.call("GET", url, key=key, expect=200).json()
    assert after["folder"] == "Archive" and "BOGUS" not in after["flags"]


# ---- idempotency: two-phase record -----------------------------------------------------
def test_pending_key_answers_conflict_and_failure_releases_it(api):
    api.publish()
    rid = api.create_run()["run"]["id"]
    started, release = threading.Event(), threading.Event()
    fake = api.evaluations
    original = FakeEvaluationService.evaluate

    def slow(scope, run_id, **kw):
        started.set()
        assert release.wait(10)
        return original(fake, scope, run_id, **kw)

    fake.evaluate = slow
    hdr = {"Idempotency-Key": "slow"}
    out = []
    t = threading.Thread(target=lambda: out.append(api.call(
        "POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={}, headers=hdr)))  # fmt: skip
    t.start()
    assert started.wait(10)
    body = err(api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={}, headers=hdr),
               409, "CONFLICT")  # fmt: skip
    assert "in progress" in body["message"]
    err(api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={"checks": "on"},
                 headers=hdr), 409, "CONFLICT")  # different request while pending  # fmt: skip
    release.set()
    t.join()
    assert out[0].status_code == 201
    again = api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={}, headers=hdr)
    assert again.headers[REPLAYED_HEADER] == "true" and again.json() == out[0].json()

    def boom(*a, **k):
        raise RuntimeError("evaluator crashed")

    fake.evaluate = boom
    r = api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={},
                 headers={"Idempotency-Key": "crash"})  # fmt: skip
    assert r.status_code == 500
    with api.sf() as session:
        assert session.get(m.IdempotencyKey, (api.tenant_id, api.project_id, "crash",
                                              _key_id(api, "evaluator"))) is None  # fmt: skip


def _key_id(api, role):
    from mailsim.api.auth import hash_key

    with api.sf() as session:
        return session.scalar(
            select(m.ApiKey.id).where(m.ApiKey.key_hash == hash_key(api.keys[role]))
        )


def test_keyed_requests_do_not_starve_a_small_pool(db_engine, tmp_path):
    small = create_engine(db_engine.url, pool_size=5, max_overflow=0, pool_timeout=10)
    try:
        api = Api(sessionmaker(small, expire_on_commit=False), tmp_path,
                  evaluation_service=FakeEvaluationService())  # fmt: skip
        api.client = TestClient(api.app, raise_server_exceptions=False)
        api.publish()
        rid = api.create_run()["run"]["id"]
        results = []

        def go(i):
            results.append(api.call("POST", f"/v1/runs/{rid}/evaluations", "evaluator", json={},
                                    headers={"Idempotency-Key": f"k{i % 20}"}))  # fmt: skip

        ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(results) == 30
        assert {r.status_code for r in results} <= {201, 409}
        assert sum(r.status_code == 201 and REPLAYED_HEADER not in r.headers for r in results) == 20
    finally:
        small.dispose()
