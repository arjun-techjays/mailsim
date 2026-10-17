"""QA-A3 regressions over HTTP with the real evaluation service: replay runs (B-6), build
naming (EV-3), incompatible answer keys (EV-4), report and artifact downloads (EV-5)."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from tests.api.conftest import KEY, Api, open_agent
from tests.scenarios import EXAMPLE_DIR

pytestmark = pytest.mark.db
ANSWER_KEY = (EXAMPLE_DIR / "answer_key.yaml").read_text()


def err(r: Any, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body = r.json()["error"]
    assert body["code"] == code
    return body


def agent_run(api: Api, *, build: str | None = "b1") -> str:
    """A run whose agent replied to the invoice (the shipped key's checks then pass)."""
    created = api.create_run(**({"agent_build": build} if build is not None else {}))
    key, mbx = api.agent(created)
    inbox = api.call("GET", f"/v1/mailboxes/{mbx}/messages?folder=inbox", key=key,
                     expect=200).json()["items"]  # fmt: skip
    invoice = next(m for m in inbox if "Invoice" in m["subject"])
    api.call("POST", f"/v1/mailboxes/{mbx}/messages/{invoice['id']}/reply", key=key,
             json={"text": "We will verify the bank details independently."}, expect=201)  # fmt: skip
    return str(created["run"]["id"])


def evaluate(api: Api, run: str, expect: int = 201, **body: Any) -> Any:
    return api.call("POST", f"/v1/runs/{run}/evaluations", "evaluator",
                    json={"complete_run": False, **body}, expect=expect)  # fmt: skip


@pytest.fixture()
def world(eval_api: Api) -> Api:
    eval_api.publish(open_agent)
    eval_api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", json={"source": ANSWER_KEY},
                  expect=201)  # fmt: skip
    return eval_api


# ---- B-6: replay runs ------------------------------------------------------------------------
def test_replay_runs_are_closed_never_graded_and_hidden_by_default(world):
    run = agent_run(world)
    report = evaluate(world, run).json()
    assert report["verdict"] in ("PASSED", "FAILED")
    replay = world.call("POST", f"/v1/runs/{run}/replay", expect=200).json()
    assert replay["matches"]
    rid = replay["replay_run_id"]
    info = world.call("GET", f"/v1/runs/{rid}", expect=200).json()
    assert info["status"] == "REPLAYED" and info["replay_of"] == run
    # the original is untouched and still live
    assert world.call("GET", f"/v1/runs/{run}", expect=200).json()["status"] == "RUNNING"

    body = err(evaluate(world, rid, expect=409), 409, "CONFLICT")
    assert "replay of" in body["message"] and body["details"] == {"run_id": rid, "replay_of": run}
    # a closed replay takes no commands and gets no agent
    err(world.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 5}), 409,
        "RUN_NOT_ACTIVE")  # fmt: skip
    err(world.call("POST", f"/v1/runs/{rid}/agent-credentials"), 409, "CONFLICT")

    ids = [r["id"] for r in world.call("GET", "/v1/runs", expect=200).json()["items"]]
    assert run in ids and rid not in ids
    every = world.call("GET", "/v1/runs?include_replays=true", expect=200).json()["items"]
    assert rid in [r["id"] for r in every]
    replays = world.call("GET", "/v1/runs?status=REPLAYED", expect=200).json()["items"]
    assert [r["id"] for r in replays] == [rid]

    # replaying a replay works and stays REPLAYED; the original's state is reproduced
    again = world.call("POST", f"/v1/runs/{rid}/replay", expect=200).json()
    assert again["matches"]
    assert world.call("GET", f"/v1/runs/{again['replay_run_id']}").json()["status"] == "REPLAYED"

    acc = world.call("GET", f"/v1/scenarios/{KEY}/accuracy", "evaluator", expect=200).json()
    assert acc["builds"]["b1"]["runs"] == 1  # the replays never count


def test_replay_of_an_ended_run_keeps_its_final_status(world):
    run = agent_run(world)
    world.call("POST", f"/v1/runs/{run}/cancel", expect=200)
    rid = world.call("POST", f"/v1/runs/{run}/replay", expect=200).json()["replay_run_id"]
    info = world.call("GET", f"/v1/runs/{rid}", expect=200).json()
    assert (info["status"], info["replay_of"]) == ("CANCELLED", run)
    err(evaluate(world, rid, expect=409), 409, "CONFLICT")


def test_stored_evaluations_of_replay_runs_are_left_out_of_accuracy(world):
    """Evaluations stored for a replay before replays were refused never count."""
    from mailsim.evaluation.service import EvaluationService

    run = agent_run(world)
    evaluate(world, run)
    rid = world.call("POST", f"/v1/runs/{run}/replay", expect=200).json()["replay_run_id"]
    service = world.evaluations
    assert isinstance(service, EvaluationService)
    original = service.list(_scope(world), run)[0]
    from mailsim.evaluation import store

    with world.sf.begin() as s:
        copy = original.model_copy(update={"evaluation_id": "evl_legacy_replay", "run_id": rid})
        store.save_evaluation(s, copy, original.reproducibility["answer_key_id"])
    acc = world.call("GET", f"/v1/scenarios/{KEY}/accuracy", "evaluator", expect=200).json()
    assert acc["builds"]["b1"]["runs"] == 1


def _scope(api: Api) -> Any:
    from mailsim.store.repos import Scope

    return Scope(api.tenant_id, api.project_id)


# ---- EV-3: runs without a build -------------------------------------------------------------
def test_no_build_is_reported_apart_from_a_build_named_unspecified(world):
    for build in (None, "unspecified"):
        evaluate(world, agent_run(world, build=build))
    acc = world.call("GET", f"/v1/scenarios/{KEY}/accuracy", "evaluator", expect=200).json()
    assert set(acc["builds"]) == {"(no build)", "unspecified"}
    assert acc["builds"]["(no build)"]["runs"] == acc["builds"]["unspecified"]["runs"] == 1
    runs = world.call("GET", "/v1/runs", expect=200).json()["items"]
    assert sorted(str(r["agent_build"]) for r in runs) == ["None", "unspecified"]
    picked = world.call("GET", f"/v1/scenarios/{KEY}/accuracy?builds=(no build)", "evaluator",
                        expect=200).json()  # fmt: skip
    assert list(picked["builds"]) == ["(no build)"]


@pytest.mark.parametrize("build", ["(no build)", "   "])
def test_reserved_and_blank_build_names_are_refused(world, build):
    r = world.call("POST", f"/v1/scenarios/{KEY}/runs", json={"agent_build": build})
    assert "agent_build" in err(r, 400, "INVALID_REQUEST")["message"]


# ---- EV-4: incompatible answer key ----------------------------------------------------------
def test_evaluating_with_an_incompatible_key_is_a_conflict_with_details(world):
    def v2(s: dict[str, Any]) -> None:
        open_agent(s)
        s["version"] = 2

    world.publish(v2)
    run = agent_run(world)  # scenario version 2; the shipped key v3 covers [1] only
    body = err(evaluate(world, run, expect=409, answer_key_version=3), 409, "CONFLICT")
    assert body["details"] == {"answer_key_version": 3, "scenario_versions": [1],
                               "run_scenario_version": 2}  # fmt: skip
    assert "not compatible" in body["message"]
    # the default (latest compatible) is the inline key of version 2
    assert evaluate(world, run).json()["scenario_version"] == 2
    err(evaluate(world, run, expect=404, answer_key_version=99), 404, "NOT_FOUND")


# ---- EV-5: downloads ------------------------------------------------------------------------
@pytest.mark.parametrize(("fmt", "ext", "ctype"), [
    ("json", "json", "application/json"),
    ("junit", "xml", "application/octet-stream"),  # XML is never echoed (renderable)
    ("markdown", "md", "text/markdown"),
])  # fmt: skip
def test_report_downloads_are_named_attachments(world, fmt, ext, ctype):
    run = agent_run(world)
    evl = evaluate(world, run).json()["evaluation_id"]
    r = world.call("GET", f"/v1/runs/{run}/evaluations/{evl}/report?format={fmt}", "evaluator",
                   expect=200)  # fmt: skip
    assert r.headers["content-type"] == ctype
    assert r.headers["content-disposition"] == f'attachment; filename="{run}-{evl}.{ext}"'
    assert r.headers["x-content-type-options"] == "nosniff"


def test_listed_artifacts_are_served_with_safe_headers(world):
    run = agent_run(world)
    evaluate(world, run)
    listed = world.call("GET", f"/v1/runs/{run}/artifacts", "evaluator", expect=200).json()
    blobs = [a for a in listed if a["kind"] != "trace"]
    assert {a["kind"] for a in blobs} == {"raw_mime", "attachment", "report"}
    assert all(a["url"] == f"/v1/runs/{run}/artifacts/{a['ref']}" for a in blobs)
    assert next(a for a in listed if a["kind"] == "trace")["url"] is None
    raw = next(a for a in blobs if a["kind"] == "raw_mime")
    for role in ("controller", "evaluator"):
        r = world.call("GET", raw["url"], role, expect=200)
        assert r.content.startswith(b"") and b"From:" in r.content
        assert r.headers["content-type"].startswith("message/rfc822")
        assert r.headers["content-disposition"] == (
            f'attachment; filename="{run}-{raw["ref"][:12]}.eml"')  # fmt: skip
        assert r.headers["content-security-policy"].startswith("sandbox")
        assert r.headers["x-content-type-options"] == "nosniff"
    report = next(a for a in blobs if a["kind"] == "report")
    r = world.call("GET", report["url"], "evaluator", expect=200)
    assert r.headers["content-disposition"].startswith(f'attachment; filename="{run}-evl_')
    # reports are evaluator-only: a controller can neither list nor fetch them
    err(world.call("GET", report["url"], "controller"), 404, "NOT_FOUND")
    err(world.call("GET", f"/v1/runs/{run}/artifacts/{'0' * 64}"), 404, "NOT_FOUND")
    err(world.call("GET", f"/v1/runs/{run}/artifacts/{f'/v1/runs/{run}/events'}"), 404,
        "NOT_FOUND")  # fmt: skip
    # another run's blob is not served through this run
    other = agent_run(world)
    err(world.call("GET", f"/v1/runs/{other}/artifacts/{report['ref']}", "evaluator"), 404,
        "NOT_FOUND")  # fmt: skip


def test_attachment_artifacts_keep_a_safe_name_and_type(world):
    run = agent_run(world)
    mbx = world.call("GET", f"/v1/runs/{run}/mailboxes", expect=200).json()
    mbx_id = (mbx if isinstance(mbx, list) else mbx["items"])[0]["id"]
    eml = (b"From: vendor@example.test\r\nTo: finance@example.test\r\nSubject: att\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\n"
           b"Content-Type: text/plain\r\n\r\nsee attached\r\n--B\r\n"
           b"Content-Type: text/html\r\nContent-Disposition: attachment; "
           b"filename*=UTF-8''r%C3%A9sum%C3%A9%20%22x%22.html\r\n\r\n<script>alert(1)</script>\r\n"
           b"--B--\r\n")  # fmt: skip
    world.call("POST", f"/v1/runs/{run}/mailboxes/{mbx_id}/inject",
               json={"raw_base64": base64.b64encode(eml).decode()}, expect=201)  # fmt: skip
    listed = world.call("GET", f"/v1/runs/{run}/artifacts", expect=200).json()
    att = next(a for a in listed if a["content_type"] == "text/html")
    r = world.call("GET", att["url"], expect=200)
    assert r.content.startswith(b"<script>")
    assert (
        r.headers["content-type"] == "application/octet-stream"
    )  # recorded text/html: never echoed
    disposition = r.headers["content-disposition"]
    assert (
        disposition.startswith('attachment; filename="r_sum')
        and "filename*=UTF-8''r%C3%A9" in disposition
    )
    assert '"x"' not in disposition and r.headers["content-security-policy"].startswith("sandbox")


# ---- review follow-ups ----------------------------------------------------------------------
def test_a_legacy_replay_stored_running_is_not_live(world):
    """Replays made before REPLAYED existed stayed READY/RUNNING: still no commands, no agent."""
    from sqlalchemy import update

    from mailsim.store import models as m

    created = world.create_run(agent_build="b1")
    run = created["run"]["id"]
    key, mbx = world.agent(created)
    other = agent_run(world)
    with world.sf.begin() as s:  # what an old replay row looks like: live status + replay_of
        s.execute(update(m.Run).where(m.Run.id == run).values(replay_of=other))
    assert world.call("GET", f"/v1/runs/{run}", expect=200).json()["status"] == "RUNNING"
    err(world.call("POST", f"/v1/runs/{run}/clock/advance", json={"seconds": 5}), 409,
        "RUN_NOT_ACTIVE")  # fmt: skip
    err(world.call("POST", f"/v1/runs/{run}/cancel"), 409, "RUN_NOT_ACTIVE")
    body = err(world.call("POST", f"/v1/runs/{run}/agent-credentials"), 409, "CONFLICT")
    assert body["details"]["replay_of"] == other
    # the agent key issued before is dead, and operator reads still work
    assert world.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key).status_code == 401
    world.call("GET", f"/v1/runs/{run}/mailboxes/{mbx}/messages", expect=200)
    err(evaluate(world, run, expect=409), 409, "CONFLICT")


@pytest.mark.parametrize(("given", "served"), [
    ("application/pdf", "application/pdf"), ("IMAGE/PNG; name=x.png", "image/png"),
    ("text/plain; charset=utf-8", "text/plain"), ("text/csv", "text/csv"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("text/html", "application/octet-stream"), ("image/svg+xml", "application/octet-stream"),
    ("application/xml", "application/octet-stream"), ("text/xml", "application/octet-stream"),
    ("application/xhtml+xml", "application/octet-stream"),
    ("application/javascript", "application/octet-stream"),
    ("video/mp4", "application/octet-stream"), ("garbage", "application/octet-stream"),
    ("", "application/octet-stream"), (None, "application/octet-stream"),
])  # fmt: skip
def test_only_passive_content_types_are_echoed(given, served):
    from mailsim.api.downloads import safe_content_type, safe_download_headers

    assert safe_content_type(given) == served
    headers = safe_download_headers("../x.html", given)
    assert headers["Content-Type"] == served
    assert headers["Content-Disposition"].startswith('attachment; filename="x.html"; filename*=')
    assert safe_download_headers("", None)["Content-Disposition"] == (
        'attachment; filename="download"')  # fmt: skip
