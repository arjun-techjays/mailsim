"""Bench statistics, gate, comparison, suite discovery and runner helpers (no database)."""

import copy
import json
import shutil

import pytest

from mailsim import cli
from mailsim.bench.compare import compare
from mailsim.bench.report import render_junit, summarize
from mailsim.bench.run import SCHEMA
from mailsim.bench.suite import discover
from mailsim.domain.errors import InvalidRequest
from mailsim.runner.base import (
    MAX_REPORT_BYTES,
    MAX_REPORT_KEYS,
    clean_usage,
    read_usage_file,
    tail,
)
from mailsim.runner.command import CommandRunner
from tests.scenarios import EXAMPLE, EXAMPLE_DIR, ROOT


def _trial(n, passed, accuracy, assertions, outcome=None, actions=2):
    return {"trial": n, "run_id": f"run_{n}", "evaluation_id": f"evl_{n}",
            "outcome": outcome or ("PASSED" if passed else "FAILED"), "passed": passed,
            "verdict": "PASSED" if passed else "FAILED", "verdict_reasons": [], "error": None,
            "seed": 1, "state_hash": "h", "event_count": 3, "accuracy": accuracy,
            "assertions": assertions, "failed_assertions": [], "duration_seconds": 1.0,
            "agent_actions": actions, "virtual_duration_seconds": 0.0, "interfaces": [],
            "rounds": [{"error": None, "stderr_tail": ""}]}  # fmt: skip


def _doc(build, scenarios, min_pass_rate=1.0, max_variance=None):
    return summarize({
        "schema": SCHEMA, "build": {"id": build, "agent_model": None, "runner": {}},
        "simulator": {"version": "0", "profiles": {}}, "llm": {"provider": "stub", "model": "m"},
        "config": {"min_pass_rate": min_pass_rate, "max_variance": max_variance},
        "started_at": "2026-10-28T00:00:00+00:00", "duration_seconds": 1.0,
        "scenarios": [{"scenario_key": k, "scenario_version": 1, "scenario_version_id": "sv",
                       "spec_hash": "s", "answer_key_version": 1, "answer_key_hash": "a",
                       "path": "p", "trials": trials} for k, trials in scenarios.items()],
    })  # fmt: skip


GOOD = [_trial(1, True, 1.0, {"a": "PASS", "b": "PASS"})]
BAD = [_trial(1, False, 0.5, {"a": "PASS", "b": "FAIL"})]


def test_summarize_stats_and_gate():
    doc = _doc(
        "b",
        {"s1": GOOD, "s2": BAD + [_trial(2, True, 1.0, {"a": "PASS", "b": "PASS"})]},
        min_pass_rate=0.5,
    )
    s2 = doc["scenarios"][1]["stats"]
    assert s2["pass_rate"] == 0.5 and s2["pass_variance"] == 0.5 and s2["variance"] == 0.125
    assert s2["per_assertion_pass_rate"] == {"a": 1.0, "b": 0.5}
    assert doc["gate"]["passed"] and doc["summary"]["trials"] == 3
    assert doc["summary"]["accuracy_variance"] is not None
    one = _doc("b", {"s": GOOD})
    assert one["scenarios"][0]["stats"]["variance"] is None
    assert one["summary"]["accuracy_variance"] is None
    empty = _doc("b", {})
    assert not empty["gate"]["passed"] and empty["summary"]["pass_rate"] is None
    none = _doc("b", {"s": []})
    assert none["gate"]["failures"][0]["reason"] == "pass rate n/a is below the required 100.0%"


def test_junit_escapes_and_counts():
    err = _trial(2, False, None, {}, outcome="ERROR")
    err["error"] = "round 1: boom \x00 <x>"
    doc = _doc("b<&>", {"s": [*GOOD, *BAD, err]})
    xml = render_junit(doc).decode()
    assert 'tests="3" failures="1" errors="1"' in xml and "b&lt;&amp;&gt;" in xml
    assert "\x00" not in xml


def test_compare_statuses_tolerance_and_missing():
    base = _doc("v1", {"s1": GOOD, "s2": BAD, "gone": GOOD})
    cand = _doc("v2", {"s1": BAD, "s2": GOOD, "new": GOOD})
    out = compare(base, cand)
    assert out["summary"]["regressed"] == ["s1"] and out["summary"]["improved"] == ["s2"]
    assert out["summary"]["added"] == ["new"] and out["summary"]["removed"] == ["gone"]
    s1 = out["scenarios"][0]
    assert s1["regressions"] == ["pass_rate", "mean_accuracy", "assertion b"]
    assert s1["deltas"]["mean_agent_actions"] == 0 and not out["gate"]["passed"]
    assert compare(base, cand, tolerance=1.0)["gate"]["passed"]
    missing = compare(base, cand, tolerance=1.0, fail_on_missing=True)
    assert missing["gate"]["reasons"] == ["scenarios missing from the candidate: gone"]
    same = compare(base, copy.deepcopy(base))  # same build name on both sides
    assert same["gate"]["passed"] and same["summary"]["unchanged"] == ["gone", "s1", "s2"]


def test_compare_flags_version_mismatch_and_rejects_other_documents():
    base = _doc("v1", {"s": GOOD})
    cand = _doc("v2", {"s": GOOD})
    cand["scenarios"][0]["answer_key_version"] = 2
    out = compare(base, cand)
    assert out["summary"]["not_comparable"] == ["s"]
    assert out["scenarios"][0]["version_mismatch"] == {
        "answer_key_version": {"baseline": 1, "candidate": 2}
    }
    with pytest.raises(InvalidRequest, match="baseline is not"):
        compare([], cand)
    with pytest.raises(InvalidRequest, match="candidate is not"):
        compare(base, {"schema": "x"})


def test_compare_cli_exit_codes(tmp_path, capsys):
    a, b, bad = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "bad.json"
    a.write_text(json.dumps(_doc("v1", {"s": GOOD})))
    b.write_text(json.dumps(_doc("v2", {"s": BAD})))
    bad.write_text("{nope")
    assert cli.main(["bench", "compare", str(a), str(b), "--tolerance", "0.4"]) == 1
    assert cli.main(["bench", "compare", str(a), str(b), "--tolerance", "1"]) == 0
    capsys.readouterr()
    assert cli.main(["bench", "compare", str(a), str(bad)]) == 2
    assert "is not JSON" in json.loads(capsys.readouterr().err)["message"]
    assert cli.main(["bench", "compare", str(a), str(tmp_path / "missing.json")]) == 2
    for flag, value in (("--tolerance", "2"), ("--tolerance", "-1"), ("--tolerance", "inf")):
        with pytest.raises(SystemExit):
            cli.main(["bench", "compare", str(a), str(b), flag, value])


@pytest.mark.parametrize(
    "flag, value",
    [
        ("--timeout", "0"),
        ("--trials", "0"),
        ("--advance-seconds", "nan"),
        ("--min-pass-rate", "1.5"),
    ],
)
def test_bench_run_argument_validation(flag, value):
    with pytest.raises(SystemExit):
        cli.main(["bench", "run", "x", "--build", "b", "--agent-cmd", "true", flag, value])


def test_discover_packs_files_and_errors(tmp_path):
    suite = tmp_path / "suite"
    shutil.copytree(EXAMPLE_DIR, suite / "b-pack")
    shutil.copytree(EXAMPLE_DIR, suite / "a" / "nested")
    (suite / "b-pack" / "answer_key.yaml").unlink()
    found = discover([suite, suite / "a" / "nested"])
    assert [e.path.parent.name for e in found] == ["nested", "b-pack"]  # sorted, deduplicated
    assert found[0].answer_key is not None and found[1].answer_key is None
    [single] = discover([EXAMPLE])
    assert single.answer_key == EXAMPLE_DIR / "answer_key.yaml"
    leadership = discover([ROOT / "scenarios" / "leadership"])
    assert len(leadership) == 4 and all(e.answer_key for e in leadership)
    with pytest.raises(InvalidRequest, match="no such"):
        discover([tmp_path / "missing"])


def test_usage_reports_and_tails(tmp_path):
    assert clean_usage([1]) == {}
    big = {f"k{i:02}": i for i in range(MAX_REPORT_KEYS + 5)}
    assert len(clean_usage(big)) == MAX_REPORT_KEYS
    assert clean_usage({"s": "x" * 500, "n": None, "f": 1.5})["s"] == "x" * 200
    path = tmp_path / "r.json"
    assert read_usage_file(path) == {}
    path.write_text("{bad")
    assert read_usage_file(path) == {}
    path.write_text(" " * (MAX_REPORT_BYTES + 1))
    assert read_usage_file(path) == {}
    assert tail("a-secret-b", ("secret", "")) == "a-***-b"


def test_command_runner_environment_is_minimal():
    runner = CommandRunner(["agent", "--x"], pass_env=["EXTRA"],
                           environ={"PATH": "/bin", "MAILSIM_DATABASE_URL": "pg://", "EXTRA": "1",
                                    "CONTROLLER_KEY": "k"})  # fmt: skip
    env = runner.environment({"MAILSIM_RUN_ID": "run_1"})
    assert env == {"PATH": "/bin", "EXTRA": "1", "MAILSIM_RUN_ID": "run_1"}
    assert runner.describe()["command"] == "agent --x"
    with pytest.raises(ValueError, match="empty"):
        CommandRunner("")
