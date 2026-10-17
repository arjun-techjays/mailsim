"""Control plane: projects, scenarios, answer keys, runs, events, replay, artifacts."""

from __future__ import annotations

import base64
import json

import pytest
import yaml

from mailsim.store import models as m
from tests.api.conftest import KEY, open_agent, scenario_payload
from tests.helpers import PDF, attachment_part, multipart, text_part
from tests.scenarios import EXAMPLE_DIR, example_dict

pytestmark = pytest.mark.db

ANSWER_KEY = (EXAMPLE_DIR / "answer_key.yaml").read_text()


def err(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()["error"]
    assert body["code"] == code and body["request_id"]
    return body


# ---- projects --------------------------------------------------------------------------
def test_project_create_returns_working_keys_and_get_is_tenant_scoped(api, other_api):
    own = api.call("GET", f"/v1/projects/{api.project_id}", "evaluator", expect=200).json()
    assert own["tenant_id"] == api.tenant_id
    created = api.call("POST", "/v1/projects", json={"name": "second"}, expect=201).json()
    roles = {c["role"]: c["api_key"] for c in created["credentials"]}
    assert set(roles) == {"controller", "evaluator"} and created["tenant_id"] == api.tenant_id
    # the new keys work for the new project, and the scenario list there is empty
    api.call("GET", f"/v1/projects/{created['id']}", key=roles["controller"], expect=200)
    assert api.call("GET", "/v1/scenarios", key=roles["evaluator"], expect=200).json() == []
    # keys are project-bound: another project (same tenant) or tenant is NOT_FOUND
    err(api.call("GET", f"/v1/projects/{created['id']}"), 404, "NOT_FOUND")
    err(other_api.call("GET", f"/v1/projects/{api.project_id}"), 404, "NOT_FOUND")
    err(api.call("POST", "/v1/projects", json={"name": " "}), 400, "INVALID_REQUEST")
    err(api.call("POST", "/v1/projects", json={}), 400, "INVALID_REQUEST")


# ---- scenarios -------------------------------------------------------------------------
def test_publish_yaml_json_and_document_forms(api):
    v1 = api.publish()
    assert v1["scenario_key"] == KEY and v1["version"] == 1
    payload = scenario_payload()
    doc = yaml.safe_load(payload["source"])
    # same content again (as JSON text and as a parsed document) is idempotent
    same = api.call("POST", "/v1/scenarios", json={"source": json.dumps(doc),
                    "fixtures": payload["fixtures"]}, expect=201).json()  # fmt: skip
    assert same["id"] == v1["id"]
    same = api.call("POST", "/v1/scenarios", json={"document": doc, "fixtures": payload["fixtures"]},
                    expect=201).json()  # fmt: skip
    assert same["id"] == v1["id"]
    # different content under the same version is a CONFLICT
    doc["scenario"]["title"] = "changed"
    err(api.call("POST", "/v1/scenarios", json={"document": doc, "fixtures": payload["fixtures"]}),
        409, "CONFLICT")  # fmt: skip
    doc["scenario"]["version"] = 2
    v2 = api.call("POST", "/v1/scenarios", json={"document": doc, "fixtures": payload["fixtures"]},
                  expect=201).json()  # fmt: skip
    assert v2["version"] == 2
    got = api.call("GET", f"/v1/scenarios/{KEY}", "evaluator", expect=200).json()
    assert got["versions"] == [1, 2] and got["latest_version"]["id"] == v2["id"]


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"source": None}, "exactly one"),
        ({"document": {"a": 1}}, "exactly one"),
        ({"fixtures": {"../escape.eml": "eA=="}}, "'..' parts"),
        ({"fixtures": {"/etc/passwd": "eA=="}}, "'..' parts"),
        ({"fixtures": {"a/./b": "eA=="}}, "'..' parts"),
        ({"fixtures": {"a//b": "eA=="}}, "'..' parts"),
        ({"fixtures": {".": "eA=="}}, "'..' parts"),
        ({"fixtures": {"scenario.json/x": "eA=="}}, "collides"),
        ({"fixtures": {"a": "eA==", "a/b": "eA=="}}, "overlap"),
        ({"fixtures": {"a/b": "eA==", "a": "eA=="}}, "overlap"),
        ({"fixtures": {"fixtures/invoice-001.eml": "not base64!"}}, "base64"),
        ({"fixtures": {"scenario.yaml": "eA=="}}, "collides"),
        ({"fixtures": {f"f{i}.eml": "eA==" for i in range(201)}}, "at most"),
        ({"source": "scenario: [unclosed"}, "not valid YAML"),
    ],
)
def test_publish_rejects_bad_requests(api, change, fragment):
    payload = {**scenario_payload(), **change}
    body = err(api.call("POST", "/v1/scenarios", json=payload), 400, "INVALID_REQUEST")
    assert fragment in body["message"], body


def test_publish_rejects_oversized_fixture_and_missing_fixture(api, monkeypatch):
    monkeypatch.setattr("mailsim.scenario.validator.MAX_FIXTURE_BYTES", 3)
    body = err(api.call("POST", "/v1/scenarios", json=scenario_payload()), 400, "INVALID_REQUEST")
    assert "too large" in body["message"]
    monkeypatch.undo()
    payload = scenario_payload()
    payload["fixtures"].pop("fixtures/invoice-001.eml")
    body = err(api.call("POST", "/v1/scenarios", json=payload), 400, "INVALID_REQUEST")
    assert "fixture not found" in json.dumps(body["details"])


def test_list_and_filter_scenarios(api, other_api):
    api.publish()
    api.publish(lambda s: s.update(id="other.key", title="Quarterly Close", tags=["ops"]))
    keys = lambda r: [x["key"] for x in r.json()]  # noqa: E731
    assert keys(api.call("GET", "/v1/scenarios", expect=200)) == [KEY, "other.key"]
    assert keys(api.call("GET", "/v1/scenarios?tag=finance", expect=200)) == [KEY]
    assert keys(api.call("GET", "/v1/scenarios?q=quarterly", expect=200)) == ["other.key"]
    assert keys(api.call("GET", "/v1/scenarios?q=INVOICE&tag=finance", expect=200)) == [KEY]
    assert keys(api.call("GET", "/v1/scenarios?tag=nope", expect=200)) == []
    assert other_api.call("GET", "/v1/scenarios", expect=200).json() == []
    err(other_api.call("GET", f"/v1/scenarios/{KEY}"), 404, "NOT_FOUND")


def test_validate_reports_without_storing(api):
    ok = api.call("POST", f"/v1/scenarios/{KEY}/validate", json=scenario_payload(), expect=200)
    assert ok.json() == {"ok": True, "errors": [], "warnings": []}
    assert api.call("GET", "/v1/scenarios", expect=200).json() == []  # nothing stored
    wrong = api.call("POST", "/v1/scenarios/other.key/validate", json=scenario_payload(),
                     expect=200).json()  # fmt: skip
    assert not wrong["ok"] and wrong["errors"][0]["path"] == "scenario.id"
    bad = example_dict()
    bad["scenario"]["agent"]["mailbox"] = "nobody"
    res = api.call("POST", f"/v1/scenarios/{KEY}/validate",
                   json={"document": bad, "fixtures": scenario_payload()["fixtures"]},
                   expect=200).json()  # fmt: skip
    assert not res["ok"] and any("nobody" in e["message"] for e in res["errors"])
    parse = api.call("POST", f"/v1/scenarios/{KEY}/validate", json={"source": "a: [b"},
                     expect=200).json()  # fmt: skip
    assert not parse["ok"] and parse["errors"][0]["path"] == "<source>"
    schema = api.call("POST", f"/v1/scenarios/{KEY}/validate", json={"document": {"x": 1}},
                      expect=200).json()  # fmt: skip
    assert not schema["ok"] and schema["errors"]
    err(api.call("POST", f"/v1/scenarios/{KEY}/validate", json={}), 400, "INVALID_REQUEST")


# ---- answer keys -----------------------------------------------------------------------
def test_answer_keys_are_metadata_only_and_evaluator_listed(api):
    api.publish()
    out = api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": ANSWER_KEY},
                   expect=201).json()  # fmt: skip
    assert out["version"] == 3 and out["source"] == "file" and out["scenario_versions"] == [1]
    doc = yaml.safe_load(ANSWER_KEY)
    again = api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"document": doc},
                     expect=201).json()  # fmt: skip
    assert again["id"] == out["id"]
    listed = api.call("GET", f"/v1/scenarios/{KEY}/answer-keys", "evaluator", expect=200)
    assert [(k["version"], k["source"]) for k in listed.json()] == [(1, "inline"), (3, "file")]
    secrets = [a["requirement"] for a in doc["answer_key"]["assertions"]]
    for text in (json.dumps(out), listed.text):
        assert "assertions" not in text and not [x for x in secrets if x in text]
    err(api.call("GET", f"/v1/scenarios/{KEY}/answer-keys", "controller"), 403,
        "PERMISSION_DENIED")  # fmt: skip


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({}, "exactly one"),
        ({"source": "x", "document": {}}, "exactly one"),
        ({"document": {"answer_key_version": "1.0"}}, "validation failed"),
        ({"source": ANSWER_KEY.replace(KEY, "other.key")}, "not 'finance"),
        ({"source": "{not json"}, "not valid JSON"),
    ],
)
def test_answer_key_bad_requests(api, body, fragment):
    api.publish()
    out = err(api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json=body), 400,
              "INVALID_REQUEST")  # fmt: skip
    assert fragment in out["message"]


def test_answer_key_for_unknown_scenario_and_conflict(api):
    err(api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": ANSWER_KEY}), 404,
        "NOT_FOUND")  # fmt: skip
    api.publish()
    api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": ANSWER_KEY}, expect=201)
    changed = ANSWER_KEY.replace('checks_default: "on"', 'checks_default: "off"')
    err(api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": changed}), 409,
        "CONFLICT")  # fmt: skip


# ---- runs ------------------------------------------------------------------------------
def test_run_create_get_start_cancel_reset(api, other_api):
    err(api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}), 404, "NOT_FOUND")
    api.publish()
    err(api.call("POST", f"/v1/scenarios/{KEY}/runs", json={"scenario_version": 9}), 404,
        "NOT_FOUND")  # fmt: skip
    created = api.create_run(start=False, scenario_version=1, agent_build="b-1", checks="off")
    run = created["run"]
    assert run["status"] == "READY" and run["agent_build"] == "b-1"
    assert (run["scenario_key"], run["scenario_version"]) == (KEY, 1)
    cred = created["agent_credentials"][0]
    assert cred["mailbox_address"] == "finance@example.test" and cred["run_id"] == run["id"]
    assert cred["api_key"].startswith("msk_")
    rid = run["id"]
    assert api.call("POST", f"/v1/runs/{rid}/start", expect=200).json()["status"] == "RUNNING"
    err(api.call("POST", f"/v1/runs/{rid}/start"), 409, "CONFLICT")
    assert api.call("GET", f"/v1/runs/{rid}", "evaluator", expect=200).json()["status"] == "RUNNING"
    reset = api.call("POST", f"/v1/runs/{rid}/reset", expect=200).json()
    assert reset["epoch"] == 1
    assert api.call("POST", f"/v1/runs/{rid}/cancel", expect=200).json()["status"] == "CANCELLED"
    err(api.call("POST", f"/v1/runs/{rid}/reset"), 409, "CONFLICT")
    # cross-tenant: every run route answers NOT_FOUND
    for method, path in (("GET", ""), ("POST", "/cancel"), ("GET", "/events"), ("GET", "/state"),
                         ("GET", "/artifacts"), ("POST", "/replay"), ("GET", "/actions")):  # fmt: skip
        err(other_api.call(method, f"/v1/runs/{rid}{path}"), 404, "NOT_FOUND")
    err(api.call("GET", "/v1/runs/run_missing"), 404, "NOT_FOUND")


def test_invalid_stored_scenario_fails_run_creation(api, monkeypatch):
    api.publish()
    with api.sf.begin() as s:  # simulate a fixture blob that went missing from storage
        sv = (
            s.query(m.ScenarioVersion)
            .join(m.Scenario)
            .filter(m.Scenario.tenant_id == api.tenant_id)
            .one()
        )
        refs = list(sv.fixtures.values())
    monkeypatch.setattr(api.blobs, "exists", lambda key: key not in refs)
    body = err(api.call("POST", f"/v1/scenarios/{KEY}/runs", json={}), 400, "INVALID_REQUEST")
    assert body["details"]["errors"]


def test_agent_credentials_route_issues_a_fresh_working_key(api):
    api.publish()
    created = api.create_run()
    rid, (key1, mbx) = created["run"]["id"], api.agent(created)
    fresh = api.call("POST", f"/v1/runs/{rid}/agent-credentials", expect=201).json()
    assert fresh["api_key"] != key1 and fresh["mailbox_id"] == mbx
    assert fresh["key_id"].startswith("key_")
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=fresh["api_key"], expect=200)
    # minting never revokes: the earlier key keeps working until revoked explicitly
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key1, expect=200)
    api.call("DELETE", f"/v1/keys/{fresh['key_id']}", expect=204)
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=fresh["api_key"], expect=401)
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key1, expect=200)


def test_events_pagination_is_complete_and_ordered(api):
    api.publish()
    rid = api.create_run()["run"]["id"]
    api.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 700}, expect=200)
    full = api.call("GET", f"/v1/runs/{rid}/events?page_size=500", "evaluator", expect=200).json()
    assert full["next_cursor"] is None
    seen, cursor = [], None
    while True:
        q = f"?page_size=4&cursor={cursor}" if cursor else "?page_size=4"
        page = api.call("GET", f"/v1/runs/{rid}/events{q}", expect=200).json()
        assert len(page["items"]) <= 4
        seen += [e["sequence"] for e in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [e["sequence"] for e in full["items"]] == list(range(1, len(seen) + 1))
    assert any(e["virtual_time"] for e in full["items"])
    for bad in ("%%%", base64.urlsafe_b64encode(b"[1]").decode(),
                base64.urlsafe_b64encode(b'{"after": -1}').decode(),
                base64.urlsafe_b64encode(b'{"after": "1"}').decode()):  # fmt: skip
        err(api.call("GET", f"/v1/runs/{rid}/events?cursor={bad}"), 400, "INVALID_REQUEST")
    err(api.call("GET", f"/v1/runs/{rid}/events?page_size=0"), 400, "INVALID_REQUEST")


def test_replay_matches(api):
    api.publish()
    created = api.create_run()
    rid, (key, mbx) = created["run"]["id"], api.agent(created)
    first = api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key, expect=200).json()["items"][0]
    api.call("POST", f"/v1/mailboxes/{mbx}/messages/{first['id']}/reply", key=key,
             json={"text": "we will verify"}, expect=201)  # fmt: skip
    out = api.call("POST", f"/v1/runs/{rid}/replay", expect=200).json()
    assert out["matches"] and out["original_hash"] == out["replay_hash"] and out["event_count"] > 0
    replay = api.call("GET", f"/v1/runs/{out['replay_run_id']}", expect=200).json()
    assert replay["replay_of"] == rid


def test_artifacts_list_mime_attachments_and_reports_for_evaluators_only(api):
    api.publish(open_agent)
    created = api.create_run()
    rid, (key, mbx) = created["run"]["id"], api.agent(created)
    raw = multipart([text_part("see attached"), attachment_part("inv.pdf", "application/pdf", PDF)])
    api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject",
             json={"raw_base64": base64.b64encode(raw).decode()}, expect=201)  # fmt: skip
    api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key,
             json={"to": ["vendor@example.test"], "subject": "s", "text": "t"}, expect=201)  # fmt: skip
    with api.sf.begin() as s:
        s.add(m.Evaluation(id="evl_art", run_id=rid, checks_enabled=True, verdict="PASSED",
                           report={}))  # fmt: skip
        s.flush()
        s.add_all([m.ReportRow(id="rep_1", evaluation_id="evl_art", format="junit", blob_ref="b" * 64),
                   m.ReportRow(id="rep_2", evaluation_id="evl_art", format="pdf", blob_ref="c" * 64)])  # fmt: skip
    ctl = api.call("GET", f"/v1/runs/{rid}/artifacts", expect=200).json()
    ev = api.call("GET", f"/v1/runs/{rid}/artifacts", "evaluator", expect=200).json()
    kinds = lambda xs: [x["kind"] for x in xs]  # noqa: E731
    assert kinds(ctl)[0] == "trace" and "report" not in kinds(ctl)
    assert {"raw_mime", "attachment"} <= set(kinds(ctl))
    refs = [x["ref"] for x in ctl]
    assert len(refs) == len(set(refs)), "shared blobs are listed once"
    pdf = [x for x in ctl if x["kind"] == "attachment"]
    assert any(x["content_type"] == "application/pdf" and x["size"] == len(PDF) for x in pdf)
    reports = [x for x in ev if x["kind"] == "report"]
    assert [x["content_type"] for x in reports] == ["application/xml", "application/octet-stream"]
    assert kinds(ev)[: len(ctl)] == kinds(ctl)


def test_me_lists_and_filters(api):
    api.publish()
    created = api.create_run()
    key, mbx = api.agent(created)
    me = api.call("GET", "/v1/me", key=key, expect=200).json()
    assert (me["role"], me["run_id"], me["mailbox_id"]) == ("agent", created["run"]["id"], mbx)
    assert me["key_id"].startswith("key_") and me["project_id"] == api.project_id
    ctl = api.call("GET", "/v1/me", expect=200).json()
    assert ctl["role"] == "controller" and ctl["run_id"] is None


def test_list_runs_newest_first_with_filters_and_pages(api, other_api):
    api.publish()
    api.publish(lambda s: s.update(id="other.key"))
    ids = [api.create_run(start=i % 2 == 0)["run"]["id"] for i in range(5)]
    other = api.call("POST", "/v1/scenarios/other.key/runs", json={}, expect=201).json()["run"][
        "id"
    ]
    newest_first = [other, *reversed(ids)]
    seen, cursor = [], None
    while True:
        q = "?page_size=2" + (f"&cursor={cursor}" if cursor else "")
        page = api.call("GET", f"/v1/runs{q}", "evaluator", expect=200).json()
        seen += [r["id"] for r in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == newest_first
    by_key = api.call("GET", f"/v1/runs?scenario_key={KEY}&status=READY", expect=200).json()
    assert [r["id"] for r in by_key["items"]] == [ids[3], ids[1]]
    assert by_key["items"][0]["scenario_key"] == KEY and by_key["items"][0]["scenario_version"] == 1
    assert other_api.call("GET", "/v1/runs", expect=200).json() == {
        "items": [],
        "next_cursor": None,
    }
    err(api.call("GET", "/v1/runs?cursor=@@"), 400, "INVALID_REQUEST")


def test_event_filters_and_action_fields(api):
    api.publish()
    created = api.create_run()
    rid, (key, mbx) = created["run"]["id"], api.agent(created)
    api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key, expect=200)
    api.call("POST", f"/v1/runs/{rid}/reset", expect=200)
    url = f"/v1/runs/{rid}/events?page_size=500"
    resets = api.call("GET", url + "&type=run.reset", expect=200).json()["items"]
    assert [e["type"] for e in resets] == ["run.reset"]
    seeded = api.call("GET", url + "&actor=simulator&epoch=1", expect=200).json()["items"]
    assert seeded and all(e["actor"] == "simulator" and e["epoch"] == 1 for e in seeded)
    first = api.call(
        "GET", f"/v1/runs/{rid}/events?page_size=1&actor=controller", expect=200
    ).json()
    nxt = api.call("GET", f"/v1/runs/{rid}/events?page_size=1&actor=controller"
                   f"&cursor={first['next_cursor']}", expect=200).json()  # fmt: skip
    assert nxt["items"][0]["sequence"] > first["items"][0]["sequence"]
    assert nxt["items"][0]["actor"] == "controller"
    acts = api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json()
    assert all(isinstance(a["id"], int) for a in acts)
    assert {"virtual_time", "seq_start", "seq_end"} <= set(acts[0])
