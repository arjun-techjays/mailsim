"""UX authoring routes: meta, system status, YAML tools, scenario drafts, .eml fixtures,
version documents and answer-key validation / documents."""

from __future__ import annotations

import base64
import copy
from email import message_from_bytes
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from mailsim.api import authoring
from mailsim.api.local_mode import LocalAccess
from mailsim.auth.principal import Principal, Role
from mailsim.scenario.answer_key import AnswerKeyDoc
from mailsim.serve import mount_adapters
from tests.api.conftest import KEY, scenario_payload
from tests.scenarios import EXAMPLE_DIR, example_dict

pytestmark = pytest.mark.db
ANSWER_KEY = yaml.safe_load((EXAMPLE_DIR / "answer_key.yaml").read_text())


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def fixtures() -> dict[str, str]:
    return scenario_payload()["fixtures"]


# ---- meta / status ---------------------------------------------------------------------------
def test_authoring_meta_lists_schemas_and_enumerations(api):
    meta = api.call("GET", "/v1/meta/authoring", "evaluator", expect=200).json()
    assert meta["scenario_schema"]["title"] == "ScenarioDoc"
    assert meta["answer_key_schema"]["title"] == "AnswerKeyDoc"
    assert meta["schema_versions"] == ["1.0", "1.1"]
    assert "reply" in meta["agent_operations"] and "send_reply" in meta["trigger_actions"]
    assert meta["trigger_types"] == ["at", "after_seconds", "after_agent_action"]
    assert meta["external_delivery_modes"] == ["deny", "sink", "event"]
    assert meta["behavior_modes"] == ["silent", "scripted", "llm"]
    types = {t["type"]: t for t in meta["assertion_types"]}
    assert types["semantic"]["check_schema"] is None
    assert types["semantic"]["default_category"] == "communication"
    assert types["state"]["check_schema"]["title"] == "StateCheck"
    assert "from" in types["state"]["check_schema"]["$defs"]["MessageWhere"]["properties"]
    assert meta["severities"] == ["mandatory", "advisory"]
    assert "no_external_recipients" in meta["invariants"]
    assert meta["folders"] == ["inbox", "sent", "drafts", "trash", "archive"]
    assert meta["flags"] == sorted(meta["flags"]) and "SEEN" in meta["flags"]


class FakeListeners:
    host, imap_port, smtp_port, tls = "127.0.0.1", 1143, 1587, True

    def __init__(self, running: bool = True, result=(True, None, "authenticated")) -> None:
        self.running, self.result, self.calls = running, result, []

    def check_login(self, username, password, *, interface="imap"):
        self.calls.append((username, interface))
        return self.result


def test_system_status_defaults(api, monkeypatch):
    monkeypatch.delenv("MAILSIM_LLM_PROVIDER", raising=False)
    st = api.call("GET", "/v1/system/status", "controller", expect=200).json()
    assert st["local_no_auth"] is False and st["version"]
    assert st["llm_provider"] == "stub" and st["llm_model"]
    assert st["interfaces"] == ["rest", "mcp_stdio"]
    assert st["listeners"] == {
        "imap": {"running": False, "host": None, "port": None, "tls": "none"},
        "smtp": {"running": False, "host": None, "port": None, "tls": "none"},
    }


def test_system_status_with_adapters_listeners_gateway_and_local_mode(api):
    mount_adapters(api.app)
    api.app.state.protocols = FakeListeners()
    api.app.state.llm_gateway = SimpleNamespace(provider=SimpleNamespace(name="claude_cli",
                                                                         model=""))  # fmt: skip
    st = api.call("GET", "/v1/system/status", "evaluator", expect=200).json()
    assert st["interfaces"] == ["rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp"]
    assert st["listeners"]["smtp"] == {"running": True, "host": "127.0.0.1", "port": 1587,
                                       "tls": "starttls"}  # fmt: skip
    assert (st["llm_provider"], st["llm_model"]) == ("claude_cli", None)
    api.app.state.protocols = FakeListeners(running=False)
    st = api.call("GET", "/v1/system/status", "evaluator", expect=200).json()
    assert st["listeners"]["imap"]["running"] is False and "imap" not in st["interfaces"]
    op = Principal(api.tenant_id, api.project_id, Role.CONTROLLER, key_id="local-operator",
                   local_operator=True)  # fmt: skip
    api.app.state.local_access = LocalAccess(op, "tok")
    client = TestClient(api.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000))
    r = client.get("/v1/system/status", headers={"x-mailsim-local-token": "tok"})
    assert r.status_code == 200 and r.json()["local_no_auth"] is True


def test_system_status_with_a_broken_llm_config(api, monkeypatch):
    monkeypatch.setenv("MAILSIM_LLM_PROVIDER", "nope")
    st = api.call("GET", "/v1/system/status", "controller", expect=200).json()
    assert (st["llm_provider"], st["llm_model"]) == ("unconfigured", None)


# ---- YAML tools ------------------------------------------------------------------------------
def test_yaml_to_json_and_back_keeps_key_order(api):
    source = "b: 1\na:\n  - x: 2\n    é: ü\nwhen: 2026-10-01T09:00:00Z\n"
    doc = api.call("POST", "/v1/tools/yaml-to-json", json={"source": source},
                   expect=200).json()  # fmt: skip
    assert doc == {"document": {"b": 1, "a": [{"x": 2, "é": "ü"}],
                                "when": "2026-10-01 09:00:00+00:00"}, "error": None}  # fmt: skip
    out = api.call("POST", "/v1/tools/json-to-yaml", "evaluator",
                   json={"document": {"z": 1, "a": {"y": [1, "ü"]}}}, expect=200).json()  # fmt: skip
    assert out["source"] == "z: 1\na:\n  y:\n  - 1\n  - ü\n"
    js = api.call("POST", "/v1/tools/yaml-to-json", json={"source": '{"k": [1]}'}).json()
    assert js["document"] == {"k": [1]}


@pytest.mark.parametrize(("source", "needle"), [
    ("a: [1,\nb: 2\n", "line"),
    ("- 1\n- 2\n", "found list"),
    ("", "found nothing"),
    ("a: &x 1\nb: *x\n", "anchors"),
    ('{"a": ', "JSON"),
])  # fmt: skip
def test_yaml_to_json_reports_errors_in_the_body(api, source, needle):
    r = api.call("POST", "/v1/tools/yaml-to-json", json={"source": source}, expect=200).json()
    assert r["document"] is None and (
        r["error"]["path"] == "<source>" or r["error"]["path"].startswith("line ")
    )
    assert needle in r["error"]["message"]


def test_yaml_to_json_error_lines_are_one_based(api):
    r = api.call("POST", "/v1/tools/yaml-to-json", json={"source": "a: 1\nb: 2\nc: d: e\n"}).json()
    assert r["error"]["path"] == "line 3"
    r = api.call("POST", "/v1/tools/yaml-to-json", json={"source": '{"a": 1,\n\n'}).json()
    assert r["error"]["path"] == "line 3"
    r = api.call("POST", "/v1/tools/yaml-to-json", json={"source": "x" * 2_000_000}).json()
    assert r["error"]["path"] == "<source>"


def test_json_to_yaml_refuses_oversized_output(api, monkeypatch):
    monkeypatch.setattr("mailsim.api.schemas.MAX_SOURCE_CHARS", 10)
    r = api.call("POST", "/v1/tools/json-to-yaml", json={"document": {"a": "x" * 50}}, expect=400)
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


# ---- scenario drafts -------------------------------------------------------------------------
def test_draft_validation_ok_and_dotted_error_paths(api):
    payload = scenario_payload()
    r = api.call("POST", "/v1/scenario-drafts/validate", json=payload, expect=200).json()
    assert r["ok"] and r["errors"] == [] and r["document"]["scenario"]["id"] == KEY
    doc = example_dict()
    doc["scenario"]["personas"][1]["email"] = "not-an-address"
    doc["scenario"]["personas"][0]["behavior"] = {"mode": "silent", "bogus": 1}
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"document": doc, "fixtures": payload["fixtures"]}, expect=200).json()  # fmt: skip
    assert not r["ok"] and r["document"] == doc
    assert {e["path"] for e in r["errors"]} == {"scenario.personas.0.behavior.bogus"}
    del doc["scenario"]["personas"][0]["behavior"]
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"document": doc, "fixtures": payload["fixtures"]}, expect=200).json()  # fmt: skip
    assert [e["path"] for e in r["errors"]] == ["scenario.personas.1.email"]


def test_draft_validation_of_unparseable_text_missing_fixtures_and_bad_requests(api):
    r = api.call("POST", "/v1/scenario-drafts/validate", json={"source": "a: [\n"}).json()
    assert not r["ok"] and r["errors"][0]["path"] == "<source>" and r["document"] is None
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"source": scenario_payload()["source"]}, expect=200).json()  # fmt: skip
    paths = [e["path"] for e in r["errors"]]
    assert "scenario.mailboxes.0.initial_messages.0.fixture" in paths
    r = api.call("POST", "/v1/scenario-drafts/validate", json={"source": "[1]"}).json()
    assert r["errors"][0]["path"] == "<root>" and r["document"] is None
    for bad in ({}, {"source": "a: 1", "document": {}},
                {"document": {}, "fixtures": {"f.eml": "%%%"}},
                {"document": {}, "fixtures": {"../f.eml": b64(b"x")}},
                {"document": {}, "fixtures": {"/abs.eml": b64(b"x")}},
                {"document": {}, "fixtures": {"a" * 300: b64(b"x")}},
                {"document": {}, "fixtures": {"scenario.yaml": b64(b"x")}},
                {"document": {}, "fixtures": {"a": b64(b"x"), "a/b": b64(b"y")}}):  # fmt: skip
        r = api.call("POST", "/v1/scenario-drafts/validate", json=bad, expect=400)
        assert r.json()["error"]["code"] == "INVALID_REQUEST", bad


def test_scenario_validate_route_shares_the_draft_code(api):
    doc = example_dict()
    doc["scenario"]["personas"][1]["email"] = "nope"
    r = api.call("POST", f"/v1/scenarios/{KEY}/validate",
                 json={"document": doc, "fixtures": fixtures()}, expect=200).json()  # fmt: skip
    assert [e["path"] for e in r["errors"]] == ["scenario.personas.1.email"]


def test_fixture_limits(monkeypatch):
    monkeypatch.setattr(authoring, "MAX_FIXTURES", 1)
    with pytest.raises(Exception, match="at most 1 fixtures"):
        authoring._decode_fixtures({"a": "", "b": ""})
    monkeypatch.setattr(authoring, "MAX_FIXTURES", 10)
    monkeypatch.setattr(authoring, "MAX_FIXTURE_TOTAL_BYTES", 3)
    with pytest.raises(Exception, match="in total"):
        authoring._decode_fixtures({"a": b64(b"xx"), "b": b64(b"yy")})
    monkeypatch.setattr("mailsim.api.schemas.MAX_FIXTURE_B64_CHARS", 2)
    with pytest.raises(Exception, match="too large"):
        authoring._decode_fixtures({"a": b64(b"xxxx")})
    monkeypatch.setattr("mailsim.api.schemas.MAX_FIXTURE_B64_CHARS", 100)
    monkeypatch.setattr("mailsim.scenario.validator.MAX_FIXTURE_BYTES", 1)
    with pytest.raises(Exception, match="too large"):
        authoring._decode_fixtures({"a": b64(b"xxxx")})


# ---- .eml fixtures ---------------------------------------------------------------------------
def compose(api, expect=200, **fields):
    body = {"from": "Vendor Ops <Vendor@Example.test>", "to": ["finance@example.test"],
            "subject": "Updated bank details", "text": "Please use the new account."}  # fmt: skip
    body.update(fields)
    return api.call("POST", "/v1/scenario-drafts/compose-eml", json=body, expect=expect).json()


def test_compose_eml_builds_a_threaded_multipart_message(api):
    out = compose(api, cc=["Boss <boss@example.test>"], html="<p>Please <b>use</b> it</p>",
                  date="2026-10-01T09:00:00Z", in_reply_to="orig-1@example.test",
                  references=["<root@example.test>"],
                  attachments=[{"filename": "../inv.pdf", "content_type": "application/pdf",
                                "content_b64": b64(b"%PDF-1.4")}])  # fmt: skip
    raw = base64.b64decode(out["content_b64"])
    assert out["size"] == len(raw) and out["path"].startswith("fixtures/updated-bank-details-")
    msg = message_from_bytes(raw)
    assert (
        msg["From"] == "Vendor Ops <vendor@example.test>"
        and msg["Cc"] == "Boss <boss@example.test>"
    )
    assert msg["Message-ID"] == out["message_id"] and out["message_id"].endswith("@example.test>")
    assert msg["In-Reply-To"] == "<orig-1@example.test>"
    assert msg["References"] == "<root@example.test> <orig-1@example.test>"
    assert msg["Date"] == "Thu, 01 Oct 2026 09:00:00 +0000"
    types = [p.get_content_type() for p in msg.walk()]
    assert {"text/plain", "text/html", "application/pdf"} <= set(types)
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["inv.pdf"]
    summary = api.call("POST", "/v1/scenario-drafts/inspect-eml",
                       json={"content_b64": out["content_b64"]}, expect=200).json()  # fmt: skip
    assert summary == {
        "message_id": out["message_id"], "from": "Vendor Ops <vendor@example.test>",
        "to": ["finance@example.test"], "cc": ["Boss <boss@example.test>"],
        "subject": "Updated bank details", "date": "2026-10-01T09:00:00Z",
        "in_reply_to": "<orig-1@example.test>", "attachments": ["inv.pdf"],
        "text_preview": "Please use the new account.\n",
    }  # fmt: skip


def test_composed_fixture_validates_inside_a_draft(api):
    out = compose(api, message_id="<given@example.test>", fixture_path="fixtures/new.eml")
    assert out["message_id"] == "<given@example.test>" and out["path"] == "fixtures/new.eml"
    doc = example_dict()
    doc["scenario"]["mailboxes"][0]["initial_messages"].append({"fixture": out["path"]})
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"document": doc, "fixtures": {**fixtures(), out["path"]: out["content_b64"]}},
                 expect=200).json()  # fmt: skip
    assert r["ok"], r["errors"]


def test_compose_defaults(api):
    out = compose(api, subject="", text=None)
    msg = message_from_bytes(base64.b64decode(out["content_b64"]))
    assert out["path"].startswith("fixtures/message-") and msg["Date"]
    assert msg.get_payload() == "\n" or msg.get_content_type() == "text/plain"


@pytest.mark.parametrize("fields", [
    {"subject": "Hi\r\nBcc: evil@example.test"},
    {"from": "a@example.test\nBcc: x@example.test"},
    {"to": ["b@example.test\r\n"]},
    {"message_id": "<a@b>\r\nX: y"},
    {"references": ["<a@b>\nX: y"]},
    {"date": "2026-10-01\n"},
    {"fixture_path": "fixtures/a\n.eml"},
    {"attachments": [{"filename": "a\r\n.pdf", "content_b64": ""}]},
    {"attachments": [{"filename": "a.pdf", "content_type": "text/plain\nX: y", "content_b64": ""}]},
])  # fmt: skip
def test_compose_rejects_header_injection(api, fields):
    r = compose(api, expect=400, **fields)
    assert r["error"]["code"] == "INVALID_REQUEST" and "header injection" in r["error"]["message"]


@pytest.mark.parametrize(("fields", "needle"), [
    ({"from": "nobody"}, "not an email address"),
    ({"cc": ["two words@x"]}, "not an email address"),
    ({"date": "yesterday"}, "ISO 8601"),
    ({"message_id": "no-at-sign"}, "Message-ID"),
    ({"in_reply_to": "<a b@c>"}, "Message-ID"),
    ({"fixture_path": "../escape.eml"}, "fixture path"),
    ({"fixture_path": "scenario.yaml"}, "collides"),
    ({"attachments": [{"filename": "a", "content_type": "bogus", "content_b64": ""}]}, "MIME type"),
    ({"attachments": [{"filename": "a", "content_b64": "%%%"}]}, "base64"),
    ({"attachments": [{"filename": "run.exe", "content_b64": b64(b"MZ")}]}, "not allowed"),
])  # fmt: skip
def test_compose_rejects_bad_fields(api, fields, needle):
    r = compose(api, expect=400, **fields)
    assert needle in r["error"]["message"]


def test_compose_size_cap_and_build_errors(api, monkeypatch):
    monkeypatch.setattr("mailsim.scenario.validator.MAX_FIXTURE_BYTES", 100)
    assert "larger than" in compose(api, expect=400)["error"]["message"]
    monkeypatch.undo()

    def boom(**_kw):
        raise ValueError("bad header")

    monkeypatch.setattr(authoring, "build_mime", boom)
    assert "cannot build" in compose(api, expect=400)["error"]["message"]


def test_inspect_eml_html_only_and_errors(api):
    raw = (b"From: a@example.test\r\nTo: b@example.test\r\nSubject: s\r\n"
           b"Content-Type: text/html\r\n\r\n<p>Hello <b>there</b></p>" + b"<i>x</i>" * 400)  # fmt: skip
    r = api.call("POST", "/v1/scenario-drafts/inspect-eml", json={"content_b64": b64(raw)},
                 expect=200).json()  # fmt: skip
    assert r["text_preview"].startswith("Hello there x") and len(r["text_preview"]) == 500
    assert r["message_id"] is None and r["date"] is None and r["from"] == "a@example.test"
    for content in ("%%%", b64(b"   "), b64(b"just some text, not a message")):
        api.call("POST", "/v1/scenario-drafts/inspect-eml", json={"content_b64": content},
                 expect=400)  # fmt: skip


def test_inspect_eml_size_cap(api, monkeypatch):
    monkeypatch.setattr("mailsim.scenario.validator.MAX_FIXTURE_BYTES", 3)
    r = api.call("POST", "/v1/scenario-drafts/inspect-eml", json={"content_b64": b64(b"abcd")},
                 expect=400)  # fmt: skip
    assert "larger than" in r.json()["error"]["message"]


# ---- published version documents ------------------------------------------------------------
def test_version_document_round_trips(api, other_api, caplog):
    api.publish()
    doc = api.call("GET", f"/v1/scenarios/{KEY}/versions/1/document", expect=200).json()
    assert doc["scenario_key"] == KEY and doc["version"] == 1 and doc["spec_hash"]
    assert doc["document"]["scenario"]["evaluation"]["assertions"] == []  # stored = stripped
    assert doc["fixtures"] == fixtures()
    again = api.call("POST", "/v1/scenarios",
                     json={"document": doc["document"], "fixtures": doc["fixtures"]},
                     expect=201).json()  # fmt: skip
    assert again["spec_hash"] == doc["spec_hash"]  # republishing the clone is idempotent
    api.call("GET", f"/v1/scenarios/{KEY}/versions/9/document", expect=404)
    other_api.call("GET", f"/v1/scenarios/{KEY}/versions/1/document", expect=404)
    for ref in list(api.blobs.root.rglob("*")):
        if ref.is_file():
            ref.unlink()
    doc = api.call("GET", f"/v1/scenarios/{KEY}/versions/1/document", expect=200).json()
    assert doc["fixtures"] == {} and "missing from the blob store" in caplog.text


# ---- answer keys -----------------------------------------------------------------------------
def validate_key(api, document=None, role="evaluator", expect=200, **body):
    if document is not None:
        body["document"] = document
    return api.call("POST", f"/v1/scenarios/{KEY}/answer-keys/validate", role, json=body,
                    expect=expect).json()  # fmt: skip


def test_answer_key_validation(api):
    api.publish()
    ok = validate_key(api, ANSWER_KEY)
    assert ok["ok"] and ok["errors"] == [] and ok["document"] == ANSWER_KEY
    ok = validate_key(api, role="controller",
                      source=(EXAMPLE_DIR / "answer_key.yaml").read_text())  # fmt: skip
    assert ok["ok"]

    bad = copy.deepcopy(ANSWER_KEY)
    bad["answer_key"]["assertions"][0]["check"] = {"operation": "reply", "count": {"min": -1}}
    r = validate_key(api, bad)
    assert not r["ok"] and r["errors"][0]["path"] == "answer_key.assertions.0.check.count.min"
    bad["answer_key"]["assertions"][0]["severity"] = "sometimes"
    r = validate_key(api, bad)
    assert [e["path"] for e in r["errors"]] == ["answer_key.assertions.0.severity"]

    other = copy.deepcopy(ANSWER_KEY)
    other["answer_key"].update(scenario="other.key", scenario_versions=[1, 7], assertions=[])
    r = validate_key(api, other)
    assert [e["path"] for e in r["errors"]] == ["answer_key.scenario",
                                                "answer_key.scenario_versions"]  # fmt: skip
    assert r["warnings"][0]["path"] == "answer_key.assertions"

    r = validate_key(api, source="answer_key: [\n")
    assert r["errors"][0]["path"] == "<source>" and r["document"] is None
    validate_key(api, expect=400)
    validate_key(api, ANSWER_KEY, expect=400, source="x: 1")
    api.call("POST", "/v1/scenarios/nope/answer-keys/validate", "evaluator",
             json={"document": ANSWER_KEY}, expect=404)  # fmt: skip


def test_answer_key_leak_guard_reports_each_version_without_the_full_secret(api):
    api.publish()
    leaky = copy.deepcopy(ANSWER_KEY)
    secret = "Hello, please find attached our invoice for October and the new remittance info"
    leaky["answer_key"]["scenario_versions"] = None
    leaky["answer_key"]["assertions"][0]["requirement"] = secret
    doc = example_dict()
    events = doc["scenario"]["events"]
    assert events, "the example has scheduled events"
    events[0]["event"]["body"] = "prefix " + secret + " suffix"
    doc["scenario"]["version"] = 2
    api.call("POST", "/v1/scenarios", json={"document": doc, "fixtures": fixtures()}, expect=201)
    r = validate_key(api, leaky)
    assert [e["path"] for e in r["errors"]] == ["leak:2"]
    assert secret[:60] in r["errors"][0]["message"] and secret not in r["errors"][0]["message"]


def test_answer_key_document_is_evaluator_only(api, other_api):
    api.publish()
    created = api.call("POST", f"/v1/scenarios/{KEY}/answer-keys", "evaluator",
                       json={"document": ANSWER_KEY}, expect=201).json()  # fmt: skip
    doc = api.call("GET", f"/v1/scenarios/{KEY}/answer-keys/2/document", "evaluator",
                   expect=200).json()  # fmt: skip
    full = AnswerKeyDoc.model_validate(ANSWER_KEY).model_dump(mode="json", by_alias=True)
    assert doc["document"] == full and doc["spec_hash"] == created["spec_hash"]
    api.call("GET", f"/v1/scenarios/{KEY}/answer-keys/2/document", "controller", expect=403)
    api.call("GET", f"/v1/scenarios/{KEY}/answer-keys/9/document", "evaluator", expect=404)
    other_api.call("GET", f"/v1/scenarios/{KEY}/answer-keys/2/document", "evaluator",
                   expect=404)  # fmt: skip


@pytest.mark.parametrize("email", ["a b@example.test", "Name <a@example.test>", "a@b",
                                   "a@example.test,b@example.test", "a@example.test\r\nBcc: x",
                                   "é@example.test", "x" * 250 + "@example.test"])  # fmt: skip
def test_persona_emails_are_strict_addr_specs(api, email):
    doc = example_dict()
    doc["scenario"]["personas"][1]["email"] = email
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"document": doc, "fixtures": fixtures()}, expect=200).json()  # fmt: skip
    assert "scenario.personas.1.email" in [e["path"] for e in r["errors"]]


def test_organization_domain_is_validated(api):
    doc = example_dict()
    doc["scenario"]["organization"]["domain"] = "bad domain"
    r = api.call("POST", "/v1/scenario-drafts/validate",
                 json={"document": doc, "fixtures": fixtures()}, expect=200).json()  # fmt: skip
    assert [e["path"] for e in r["errors"]] == ["scenario.organization.domain"]


def test_publish_errors_carry_dotted_issues(api):
    doc = example_dict()
    doc["scenario"]["personas"][1]["email"] = "nope"
    r = api.call("POST", "/v1/scenarios", json={"document": doc, "fixtures": fixtures()},
                 expect=400).json()["error"]  # fmt: skip
    assert r["details"]["issues"][0]["path"] == "scenario.personas.1.email"
    assert isinstance(r["details"]["errors"][0], str)
