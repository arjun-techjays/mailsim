import json

import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.scenario.loader import fixture_manifest, load_scenario, parse_source
from mailsim.scenario.schema import ScenarioDoc, canonical_json, json_schema, spec_hash
from mailsim.scenario.validator import resolve_fixture, validate_data, validate_scenario
from tests.scenarios import EXAMPLE, EXAMPLE_DIR, example_dict, write_variant


def errors_for(mutate, tmp_path, with_fixtures=True):
    data = example_dict()
    mutate(data["scenario"])
    return [str(e) for e in validate_scenario(data, EXAMPLE_DIR if with_fixtures else None).errors]


def test_example_scenario_loads_and_is_canonical():
    loaded = load_scenario(EXAMPLE)
    assert loaded.key == "finance.invoice-bank-change" and loaded.version == 1
    assert set(loaded.fixtures) == {"fixtures/invoice-001.eml", "fixtures/purchase-order-001.eml"}
    assert loaded.spec_hash == spec_hash(loaded.doc) == spec_hash(loaded.spec)
    assert json.loads(canonical_json(loaded.doc)) == loaded.spec
    assert fixture_manifest(loaded.fixtures).keys() == loaded.fixtures.keys()
    assert "properties" in json_schema()


def test_yaml_and_json_forms_are_equivalent(tmp_path):
    loaded = load_scenario(EXAMPLE)
    p = write_variant(tmp_path)
    (p.parent / "s.json").write_text(json.dumps(loaded.spec))
    assert load_scenario(p.parent / "s.json").spec_hash == loaded.spec_hash


def test_schema_rejects_unknown_keys_and_bad_version():
    d = example_dict()
    d["scenario"]["typo"] = 1
    assert not validate_data(d).ok
    d = example_dict()
    d["schema_version"] = "9"
    assert any("schema_version" in str(e) for e in validate_data(d).errors)
    assert validate_data("nonsense").errors[0].path == "<root>"


def test_external_delivery_can_never_be_enabled():
    for bad in ("allow", True):
        d = example_dict()
        d["scenario"]["environment"]["delivery"]["external_delivery"] = bad
        report = validate_data(d)
        assert not report.ok
        assert "never be enabled" in str(report.errors[0]) or "deny" in str(report.errors[0])


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda s: s["personas"].append(dict(s["personas"][0])), "duplicate persona id"),
        (
            lambda s: s["personas"][1].update(email=s["personas"][0]["email"]),
            "duplicate persona email",
        ),
        (lambda s: s["personas"][0].update(email="nope"), "not an email"),
        (
            lambda s: s["organization"]["policies"].append(dict(s["organization"]["policies"][0])),
            "duplicate policy",
        ),
        (lambda s: s["mailboxes"].append({"owner": "ghost"}), "unknown persona 'ghost'"),
        (lambda s: s["mailboxes"].append({"owner": "vendor"}), "more than one mailbox"),
        (lambda s: s["agent"].update(mailbox="nobody"), "has no mailbox"),
        (lambda s: s["agent"].update(allowed_operations=["fly"]), "unknown operation 'fly'"),
        (lambda s: s["agent"].update(prohibited_operations=["fly"]), "unknown operation 'fly'"),
        (
            lambda s: s["agent"].update(prohibited_operations=["reply"]),
            "both allowed and prohibited",
        ),
        (lambda s: s["environment"]["clock"].update(start_at="garbage"), "invalid timestamp"),
        (lambda s: s["events"].append(dict(s["events"][0])), "duplicate event id"),
        (lambda s: s["events"][0]["event"].update(to="ghost"), "unknown persona 'ghost'"),
        (lambda s: s["events"][0]["event"].update(to=[]), "at least one recipient"),
        (
            lambda s: s["events"][0]["event"].update(to="vendor") or s["mailboxes"].pop(),
            "has no mailbox",
        ),
        (lambda s: s["events"][0]["event"].update(**{"from": "ghost"}), "unknown persona 'ghost'"),
        (lambda s: s["events"][1]["trigger"].update(action="teleport"), "unknown action"),
        (lambda s: s["events"][0]["trigger"].update(type="at"), "required for trigger type 'at'"),
        (
            lambda s: s["events"][0]["trigger"].update(type="at", at="2020-01-01T00:00:00Z"),
            "before the clock start",
        ),
        (lambda s: s["events"][0]["trigger"].update(type="at", at="zzz"), "invalid timestamp"),
        (
            lambda s: s["events"][0]["trigger"].pop("seconds"),
            "required for trigger type 'after_seconds'",
        ),
        (
            lambda s: s["evaluation"]["assertions"].append(dict(s["evaluation"]["assertions"][0])),
            "duplicate assertion",
        ),
        (lambda s: s["evaluation"]["assertions"][0].update(type="vibes"), "unknown assertion type"),
        (lambda s: s["evaluation"]["limits"].update(max_agent_actions=0), "greater than 0"),
        (
            lambda s: s.setdefault("relationships", []).append(
                {"source": "vendor", "target": "ghost", "type": "x"}
            ),
            "unknown persona 'ghost'",
        ),
        (
            lambda s: s.setdefault("projects", []).append(
                {"id": "p", "name": "n", "participants": ["ghost"]}
            ),
            "unknown persona 'ghost'",
        ),
        (
            lambda s: s.setdefault("projects", []).extend(
                [{"id": "p", "name": "n"}, {"id": "p", "name": "m"}]
            ),
            "duplicate project id",
        ),
    ],
)
def test_validator_rules(mutate, needle, tmp_path):
    assert any(needle in e for e in errors_for(mutate, tmp_path)), errors_for(mutate, tmp_path)


def test_relationships_and_projects_valid():
    d = example_dict()
    d["scenario"]["relationships"] = [
        {"source": "vendor", "target": "finance_manager", "type": "supplier", "trust_level": "low"}
    ]
    d["scenario"]["projects"] = [
        {"id": "p1", "name": "Q4", "participants": ["vendor"], "facts": ["f"]}
    ]
    assert validate_scenario(d, EXAMPLE_DIR).ok


@pytest.mark.parametrize("rel", ["/etc/passwd", "../x.eml", "", "a\x00b", "fixtures/missing.eml"])
def test_fixture_path_rules(rel):
    d = example_dict()
    d["scenario"]["mailboxes"][0]["initial_messages"] = [{"fixture": rel}]
    assert not validate_scenario(d, EXAMPLE_DIR).ok


def test_resolve_fixture_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "outside.eml"
    outside.write_text("x")
    base = tmp_path / "base"
    base.mkdir()
    (base / "link.eml").symlink_to(outside)
    with pytest.raises(InvalidRequest, match="escapes"):
        resolve_fixture(base, "link.eml")


def test_bad_fixture_content(tmp_path):
    p = write_variant(tmp_path)
    fx = p.parent / "fixtures"
    (fx / "invoice-001.eml").write_bytes(b"")
    (fx / "purchase-order-001.eml").write_bytes(b"Subject: no from\r\n\r\nx\r\n")
    with pytest.raises(InvalidRequest) as ei:
        load_scenario(p)
    msgs = " ".join(ei.value.details["errors"])
    assert "not a valid message" in msgs and "no From header" in msgs


def test_oversized_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr("mailsim.scenario.validator.MAX_FIXTURE_BYTES", 10)
    with pytest.raises(InvalidRequest, match="validation failed") as ei:
        load_scenario(write_variant(tmp_path))
    assert "too large" in " ".join(ei.value.details["errors"])


def test_loader_errors(tmp_path):
    with pytest.raises(InvalidRequest, match="not found"):
        load_scenario(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed")
    with pytest.raises(InvalidRequest, match="not valid YAML"):
        load_scenario(bad)
    with pytest.raises(InvalidRequest, match="not valid JSON"):
        parse_source(b"{", is_json=True)
    with pytest.raises(InvalidRequest, match="too large"):
        parse_source(b"x" * (1024 * 1024 + 1))


def test_yaml_is_loaded_safely(tmp_path):
    evil = tmp_path / "evil.yaml"
    evil.write_text("!!python/object/apply:os.system ['echo pwned']")
    with pytest.raises(InvalidRequest):
        load_scenario(evil)


def test_validation_report_helpers():
    d = example_dict()
    d["scenario"]["agent"]["mailbox"] = "x"
    report = validate_scenario(d)
    with pytest.raises(InvalidRequest) as ei:
        report.raise_if_invalid()
    assert ei.value.details["errors"]
    assert str(report.errors[0]).startswith("scenario.agent.mailbox")
    assert ScenarioDoc.model_validate(example_dict()).scenario.id


@pytest.mark.parametrize(
    ("behavior", "needle"),
    [
        (
            {"mode": "scripted", "script": [{"on": {"from": "ghost"}, "reply": {"body": "ok"}}]},
            "unknown persona 'ghost'",
        ),
        ({"mode": "silent", "script": [{"reply": {"body": "ok"}}]}, "require mode: scripted"),
    ],
)
def test_persona_script_rules(behavior, needle, tmp_path):
    errs = errors_for(lambda s: s["personas"][1].update(behavior=behavior), tmp_path)
    assert any(needle in e for e in errs), errs


def test_scripted_persona_and_schema_1_1_valid():
    d = example_dict()
    d["schema_version"] = "1.1"
    d["scenario"]["personas"][1]["behavior"] = {
        "mode": "scripted",
        "script": [{"on": {"from": "finance_manager", "subject_contains": "verify"},
                    "reply": {"body": "Sure, call our office to verify."}, "delay_seconds": 60}],
    }  # fmt: skip
    assert validate_scenario(d, EXAMPLE_DIR).ok
