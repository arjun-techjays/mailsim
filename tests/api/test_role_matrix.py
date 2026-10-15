"""Every route x every role: 401 without/with a bad key, 403 for the wrong role (before any
side effect), 2xx for the right role. The matrix must cover every route the app serves."""

from __future__ import annotations

import pytest

from mailsim.api.auth import hash_key, revoke_key
from tests.api.conftest import KEY, Api, open_agent, scenario_payload
from tests.scenarios import EXAMPLE_DIR

pytestmark = pytest.mark.db

C, E, A = "controller", "evaluator", "agent"
ANSWER_KEY = (EXAMPLE_DIR / "answer_key.yaml").read_text()

# (method, path template, allowed roles, json body or None, success status)
ROUTES: list[tuple[str, str, set[str], object, int]] = [
    ("POST", "/v1/projects", {C}, {"name": "second"}, 201),
    ("GET", "/v1/projects/{project}", {C, E}, None, 200),
    ("POST", "/v1/scenarios", {C}, "scenario", 201),
    ("GET", "/v1/scenarios", {C, E}, None, 200),
    ("GET", "/v1/scenarios/{key}", {C, E}, None, 200),
    ("POST", "/v1/scenarios/{key}/validate", {C}, "scenario", 200),
    ("POST", "/v1/scenarios/{key}/answer-keys", {C}, {"source": ANSWER_KEY}, 201),
    ("GET", "/v1/scenarios/{key}/answer-keys", {E}, None, 200),
    ("POST", "/v1/scenarios/{key}/runs", {C}, {}, 201),
    ("GET", "/v1/scenarios/{key}/accuracy?builds=a&builds=b", {E}, None, 200),
    ("GET", "/v1/runs/{run}", {C, E}, None, 200),
    ("POST", "/v1/runs/{ready}/start", {C}, None, 200),
    ("POST", "/v1/runs/{run}/agent-credentials", {C}, None, 201),
    ("POST", "/v1/runs/{run}/cancel", {C}, None, 200),
    ("POST", "/v1/runs/{run}/reset", {C}, None, 200),
    ("GET", "/v1/runs/{run}/events", {C, E}, None, 200),
    ("POST", "/v1/runs/{run}/replay", {C}, None, 200),
    ("GET", "/v1/runs/{run}/artifacts", {C, E}, None, 200),
    ("POST", "/v1/runs/{run}/evaluations", {E}, {}, 201),
    ("GET", "/v1/runs/{run}/evaluations", {E}, None, 200),
    ("GET", "/v1/runs/{run}/results", {E}, None, 200),
    ("GET", "/v1/runs/{run}/evaluations/evl_1/report", {E}, None, 200),
    ("POST", "/v1/runs/{run}/mailboxes/{mbx}/inject", {C},
     {"message": {"from": "vendor@example.test", "to": ["finance@example.test"], "text": "x"}}, 201),
    ("POST", "/v1/runs/{run}/clock/advance", {C}, {"seconds": 5}, 200),
    ("POST", "/v1/runs/{run}/scheduler/tick", {C}, None, 200),
    ("GET", "/v1/runs/{run}/state", {C, E}, None, 200),
    ("GET", "/v1/runs/{run}/actions", {C, E}, None, 200),
    ("GET", "/v1/mailboxes/{mbx}/messages", {A}, None, 200),
    ("GET", "/v1/mailboxes/{mbx}/messages/{msg}", {A}, None, 200),
    ("GET", "/v1/mailboxes/{mbx}/threads/{thread}", {A}, None, 200),
    ("POST", "/v1/mailboxes/{mbx}/messages/{msg}/reply", {A}, {"text": "ack"}, 201),
    ("POST", "/v1/mailboxes/{mbx}/messages", {A}, {"to": ["vendor@example.test"]}, 201),
    ("PATCH", "/v1/mailboxes/{mbx}/messages/{msg}", {A}, {"add_flags": ["SEEN"]}, 200),
    ("DELETE", "/v1/mailboxes/{mbx}/messages/{msg}", {A}, None, 204),
    ("GET", "/v1/mailboxes/{mbx}/changes", {A}, None, 200),
    ("GET", "/v1/mailboxes/{mbx}/folders", {A}, None, 200),
]  # fmt: skip


def test_matrix_covers_every_route():
    from mailsim.api.routers import control, mailbox, simulator

    served = {
        (method, route.path)
        for module in (control, simulator, mailbox)
        for route in module.router.routes
        for method in route.methods - {"HEAD"}
    }
    listed = set()
    for method, path, *_ in ROUTES:
        template = path.split("?")[0]
        for name, real in (("{project}", "{project_id}"), ("{key}", "{scenario_key}"),
                           ("{ready}", "{run_id}"), ("{run}", "{run_id}"), ("{mbx}", "{mailbox_id}"),
                           ("{msg}", "{message_id}"), ("{thread}", "{thread_id}"),
                           ("evl_1", "{evaluation_id}")):  # fmt: skip
            template = template.replace(name, real)
        listed.add((method, template))
    assert listed == served


class World:
    def __init__(self, api: Api) -> None:
        self.api = api
        api.publish(open_agent)
        created = api.create_run()
        self.agent_key, mbx = api.agent(created)
        ready = api.create_run(start=False)["run"]["id"]
        run = created["run"]["id"]
        first = api.call("GET", f"/v1/mailboxes/{mbx}/messages?folder=inbox", key=self.agent_key,
                         expect=200).json()["items"][0]  # fmt: skip
        self.values = {"project": api.project_id, "key": KEY, "run": run, "ready": ready,
                       "mbx": mbx, "msg": first["id"], "thread": first["thread_id"]}  # fmt: skip
        self.keys = {**api.keys, A: self.agent_key}

    def url(self, path: str) -> str:
        return path.format(**self.values)


def _body(body: object, api: Api) -> object:
    return scenario_payload(api.tmp_path / "scn" / "scenario.yaml") if body == "scenario" else body


@pytest.mark.parametrize(("method", "path", "allowed", "body", "status"), ROUTES,
                         ids=[f"{m} {p}" for m, p, *_ in ROUTES])  # fmt: skip
def test_route_role_matrix(api, method, path, allowed, body, status):
    w = World(api)
    url, payload = w.url(path), _body(body, api)
    kwargs = {"json": payload} if payload is not None else {}

    r = api.call(method, url, role=None, **kwargs)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    for bad in ("msk_000000000000_" + "x" * 43, "not-a-key", w.agent_key + "x"):
        r = api.call(method, url, key=bad, **kwargs)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED", bad

    for role in sorted({C, E, A} - allowed):
        r = api.call(method, url, key=w.keys[role], **kwargs)
        assert r.status_code == 403, (role, r.text)
        assert r.json()["error"]["code"] == "PERMISSION_DENIED"

    for role in sorted(allowed):  # multi-role routes are all reads: one world is enough
        r = api.call(method, url, key=w.keys[role], **kwargs)
        assert r.status_code == status, (role, r.text)


def test_wrong_role_has_no_side_effects(api):
    w = World(api)
    run = w.values["run"]
    before = api.call("GET", f"/v1/runs/{run}/events?page_size=500", "evaluator").json()["items"]
    for role in (E, A):
        api.call("POST", f"/v1/runs/{run}/cancel", key=w.keys[role], expect=403)
        api.call("POST", f"/v1/runs/{run}/clock/advance", key=w.keys[role], json={"seconds": 9},
                 expect=403)  # fmt: skip
    after = api.call("GET", f"/v1/runs/{run}/events?page_size=500", "evaluator").json()["items"]
    assert after == before


def test_revoked_and_expired_keys_are_rejected(api):
    from datetime import UTC, datetime, timedelta

    from mailsim.store import models as m

    w = World(api)
    mbx = w.values["mbx"]
    url = f"/v1/mailboxes/{mbx}/folders"
    api.call("GET", url, key=w.agent_key, expect=200)
    with api.sf.begin() as s:
        row = s.query(m.ApiKey).filter(m.ApiKey.key_hash == hash_key(w.agent_key)).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    api.call("GET", url, key=w.agent_key, expect=401)
    with api.sf.begin() as s:
        row = s.query(m.ApiKey).filter(m.ApiKey.key_hash == hash_key(w.agent_key)).one()
        row.expires_at = None
    api.call("GET", url, key=w.agent_key, expect=200)
    with api.sf.begin() as s:
        revoke_key(s, row.id)
        revoke_key(s, row.id)  # idempotent
        revoke_key(s, "key_missing")
    r = api.call("GET", url, key=w.agent_key, expect=401)
    assert r.json()["error"]["message"] == "missing or invalid API key"


def test_basic_scheme_is_unauthenticated(api):
    r = api.client.get("/v1/scenarios", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
