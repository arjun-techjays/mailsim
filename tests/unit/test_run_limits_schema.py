"""Per-run resource caps (spec 10): scenario `evaluation.limits` fields, their defaults, the
canonical form they keep, and the answer key's `no_limit_exceeded` narrowing (issue #7)."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from mailsim.engine.core import run_cap
from mailsim.evaluation.checks._common import Occurrence
from mailsim.evaluation.checks.safety import _limit_name
from mailsim.scenario.answer_key import LIMIT_NAMES, LimitName, SafetyCheck
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.schema import DEFAULT_RUN_CAPS, LimitsSpec, ScenarioDoc, canonical_dict
from tests.scenarios import ROOT, example_dict

CAPS = ("max_agent_reads", "max_sent_copies", "max_agent_events", "max_labels_per_message")


def test_defaults_are_the_approved_values():
    assert DEFAULT_RUN_CAPS == {
        "max_agent_reads": 10_000,
        "max_sent_copies": 1_000,
        "max_agent_events": 20_000,
        "max_labels_per_message": 50,
    }


def test_limit_names_are_the_limits_spec_fields():
    assert set(LIMIT_NAMES) == set(LimitsSpec.model_fields)
    assert get_args(LimitName) == LIMIT_NAMES
    assert set(DEFAULT_RUN_CAPS) == set(CAPS)


# spec hashes of the bundled scenarios as computed on main before the caps existed (467d119)
PRE_CAPS_HASHES = {
    "finance.invoice-bank-change": "b7ca8f8ee2811a18e0e5fb6b61fd87aa186cdc9405cd43ca2fe57ea182696be6",
    "leadership/cbo": "6da5fcc94b2da3c3fe4c1a8f3d58ac38fa6887f6bacf8a6fe1e36cf71e45d460",
    "leadership/ceo": "82a4b531abf2106217cf8d06f5ca1ea760d4909d6f960ae4ff4b162c3c4d5645",
    "leadership/cfo": "fd9cedf917e4cc2e099b9cc55d02c756adb975edef985a762f196d9353e24d3b",
    "leadership/director-finance": "789926f561bd28769a2bac931bec39c04a7da002759e70740d92316264606bbf",
}


@pytest.mark.parametrize(("name", "digest"), sorted(PRE_CAPS_HASHES.items()))
def test_unset_caps_keep_the_canonical_form_and_hash(name: str, digest: str):
    """Scenarios that do not set a cap serialise exactly as before the caps existed, so a
    published version re-publishes without a spec-hash CONFLICT. (If a bundled scenario's
    content changes on purpose, update its pinned hash from main.)"""
    loaded = load_scenario(ROOT / "scenarios" / name / "scenario.yaml")
    limits = canonical_dict(loaded.doc)["scenario"]["evaluation"]["limits"]
    assert set(limits) == {"max_agent_actions", "max_virtual_duration_seconds"}
    assert loaded.spec_hash == digest


def test_every_bundled_scenario_is_pinned():
    found = {str(p.parent.relative_to(ROOT / "scenarios"))
             for p in (ROOT / "scenarios").rglob("scenario.yaml")}  # fmt: skip
    assert found == set(PRE_CAPS_HASHES)


def test_set_caps_are_serialised_and_change_the_form():
    data = example_dict()
    data["scenario"]["evaluation"]["limits"].update(max_agent_reads=7, max_labels_per_message=3)
    limits = canonical_dict(ScenarioDoc.model_validate(data))["scenario"]["evaluation"]["limits"]
    assert limits["max_agent_reads"] == 7 and limits["max_labels_per_message"] == 3
    assert "max_sent_copies" not in limits and "max_agent_events" not in limits


@pytest.mark.parametrize(
    ("name", "bad"),
    [("max_agent_reads", 0), ("max_agent_reads", 1_000_001), ("max_sent_copies", 100_001),
     ("max_agent_events", -1), ("max_labels_per_message", 1_001), ("max_labels_per_message", "x")],
)  # fmt: skip
def test_cap_bounds(name, bad):
    with pytest.raises(ValidationError):
        LimitsSpec.model_validate({name: bad})
    assert getattr(LimitsSpec.model_validate({name: 1}), name) == 1


def test_run_cap_defaults_for_unset_and_older_configs():
    assert run_cap({"limits": {"max_agent_reads": 5}}, "max_agent_reads") == 5
    assert run_cap({"limits": {"max_agent_actions": 100}}, "max_agent_reads") == 10_000
    assert run_cap({"limits": {"max_sent_copies": None}}, "max_sent_copies") == 1_000
    assert run_cap({}, "max_labels_per_message") == 50


def test_no_limit_exceeded_may_name_one_limit():
    assert SafetyCheck.model_validate({"invariant": "no_limit_exceeded"}).limit is None
    check = SafetyCheck.model_validate(
        {"invariant": "no_limit_exceeded", "limit": "max_agent_reads"}
    )
    assert check.limit == "max_agent_reads"
    with pytest.raises(ValidationError, match="only to the no_limit_exceeded"):
        SafetyCheck.model_validate(
            {"invariant": "no_permission_denials", "limit": "max_agent_reads"}
        )
    with pytest.raises(ValidationError):
        SafetyCheck.model_validate({"invariant": "no_limit_exceeded", "limit": "max_cookies"})


def _occ(result: dict) -> Occurrence:
    return Occurrence(id="act_1", actor="agent", operation="read", outcome="SIMULATION_LIMIT_EXCEEDED",
                      input={}, result=result, seq_start=1, seq_end=0, virtual_time=None)  # fmt: skip


def test_limit_name_of_recorded_refusals():
    assert (
        _limit_name(_occ({"details": {"limit": 3, "cap": "max_agent_reads"}})) == "max_agent_reads"
    )
    assert _limit_name(_occ({"details": {"cap": 7}})) is None
    assert _limit_name(_occ({})) is None
    # recorded before limits were named: the message names the two limits that existed then
    old_budget = {"code": "SIMULATION_LIMIT_EXCEEDED", "message": "agent action limit of 3 reached",
                  "details": {"limit": 3}}  # fmt: skip
    assert _limit_name(_occ(old_budget)) == "max_agent_actions"
    old_time = {"message": "virtual duration limit of 60s exceeded", "details": {"limit": 60}}
    assert _limit_name(_occ(old_time)) == "max_virtual_duration_seconds"
    assert _limit_name(_occ({"message": "something else", "details": {"limit": 3}})) is None
    assert _limit_name(_occ({"message": 5})) is None
