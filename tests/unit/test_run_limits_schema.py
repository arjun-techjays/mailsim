"""Per-run resource caps (spec 10): scenario `evaluation.limits` fields, their defaults, the
canonical form they keep, and the answer key's `no_limit_exceeded` narrowing (issue #7)."""

from __future__ import annotations

from pathlib import Path
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


@pytest.mark.parametrize("path", sorted((ROOT / "scenarios").rglob("scenario.yaml")), ids=str)
def test_unset_caps_keep_the_canonical_form_and_hash(path: Path):
    """Scenarios that do not set a cap serialise exactly as before the caps existed, so a
    published version re-publishes without a spec-hash CONFLICT."""
    limits = canonical_dict(load_scenario(path).doc)["scenario"]["evaluation"]["limits"]
    assert set(limits) == {"max_agent_actions", "max_virtual_duration_seconds"}


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
    assert _limit_name(_occ({"details": {"limit": 3}})) is None  # recorded before caps were named
    assert _limit_name(_occ({"details": {"cap": 7}})) is None
    assert _limit_name(_occ({})) is None
