"""`sim bench run` end to end against real PostgreSQL (issue #13 part 2, spec 9.4)."""

import json
import shlex
import sys
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import select

from mailsim import cli
from mailsim.store import models as m
from tests import bench_agents
from tests.scenarios import EXAMPLE_DIR, ROOT

pytestmark = pytest.mark.db


@pytest.fixture()
def bench_env(db_engine, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAILSIM_DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MAILSIM_LLM_PROVIDER", "stub")
    assert cli.main(["project-create"]) == 0
    project = json.loads(capsys.readouterr().out)["project_id"]
    return tmp_path, project


def bench(capsys, project, *argv):
    code = cli.main(["bench", "run", str(EXAMPLE_DIR), "--project", project, *argv])
    out = capsys.readouterr()
    return (
        code,
        (json.loads(out.out) if out.out.strip() else None),
        (json.loads(out.err) if out.err.strip() else None),
    )


def test_passing_build_records_reproducibility_and_writes_json_and_junit(bench_env, capsys):
    tmp, project = bench_env
    bench_agents.CALLS.clear()
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "agent-1.0",
        "--agent-callable",
        "tests.bench_agents:good",
        "--trials",
        "2",
        "--rounds",
        "2",
        "--advance-seconds",
        "600",
        "--agent-model",
        "acme/gpt:2026-01",
        "--json",
        str(tmp / "out" / "a.json"),
        "--junit",
        str(tmp / "a.xml"),
    )
    assert code == 0, doc
    assert doc["schema"] == "mailsim.bench/1" and doc["gate"]["passed"]
    assert doc["build"] == {
        "id": "agent-1.0",
        "agent_model": "acme/gpt:2026-01",
        "runner": {"type": "callable", "callable": "tests.bench_agents:good"},
    }
    assert doc["llm"]["provider"] == "stub" and doc["simulator"]["version"]
    assert set(doc["simulator"]["profiles"]) >= {"rest", "gmail", "graph", "imap", "smtp", "mcp"}
    assert doc["config"]["trials"] == 2 and doc["config"]["min_pass_rate"] == 1.0
    [sc] = doc["scenarios"]
    assert sc["scenario_key"] == "finance.invoice-bank-change" and sc["scenario_version"] == 1
    assert sc["answer_key_version"] == 3 and sc["spec_hash"] and sc["scenario_version_id"]
    assert sc["stats"]["runs"] == 2 and sc["stats"]["pass_rate"] == 1.0
    assert sc["stats"]["variance"] == 0.0 and sc["stats"]["pass_variance"] == 0.0
    assert sc["stats"]["per_assertion_pass_rate"]["replied-to-vendor"] == 1.0
    t1, t2 = sc["trials"]
    for t in (t1, t2):
        assert t["outcome"] == "PASSED" and t["passed"] and t["run_status"] == "PASSED"
        assert t["seed"] == 4172 and t["state_hash"] and t["event_count"] > 0
        assert t["agent_actions"] >= 2 and t["replies_sent"] == 1 and t["interfaces"] == ["api"]
        assert t["virtual_duration_seconds"] >= 600.0  # up to the last recorded event
        assert [r["clock"]["virtual_time"] for r in t["rounds"]] == [
            "2026-10-15T09:10:00Z",
            "2026-10-15T09:20:00Z",
        ]
        assert (
            t["model_usage"]["agent"]
            == [{"input_tokens": 3, "model": "scripted-good", "output_tokens": 2}] * 2
        )
        assert t["model_usage"]["gateway"]["calls"] >= 0
    assert t1["run_id"] != t2["run_id"] and t1["state_hash"] == t2["state_hash"]  # deterministic
    assert [(c["trial"], c["round"]) for c in bench_agents.CALLS] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    assert bench_agents.CALLS[0]["address"] == "finance@example.test"
    assert json.loads((tmp / "out" / "a.json").read_text()) == doc
    root = ET.parse(tmp / "a.xml").getroot()  # noqa: S314 - our own output
    assert root.get("tests") == "2" and root.get("failures") == "0" and root.get("errors") == "0"
    suite = root.find("testsuite")
    props = {p.get("name"): p.get("value") for p in suite.iter("property")}
    assert props["agent_build"] == "agent-1.0" and props["pass_rate"] == "1.0000"
    assert props["gate"] == "passed" and props["llm"].startswith("stub")
    assert [c.get("name") for c in suite.iter("testcase")] == ["trial-1", "trial-2"]
    # a second bench of the unchanged pack reuses the immutable versions
    code, doc2, _ = bench(
        capsys, project, "--build", "agent-1.0", "--agent-callable", "tests.bench_agents:good"
    )
    assert code == 0 and doc2["scenarios"][0]["scenario_version_id"] == sc["scenario_version_id"]


def test_failing_build_fails_the_gate(bench_env, capsys):
    tmp, project = bench_env
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "silent",
        "--agent-callable",
        "tests.bench_agents:silent",
        "--junit",
        str(tmp / "s.xml"),
    )
    assert code == 1 and not doc["gate"]["passed"]
    [t] = doc["scenarios"][0]["trials"]
    assert t["outcome"] == "FAILED" and "replied-to-vendor" in t["failed_assertions"]
    assert doc["gate"]["failures"][0]["reason"].startswith("pass rate 0.0%")
    case = ET.parse(tmp / "s.xml").getroot().find("testsuite/testcase")  # noqa: S314
    assert case.find("failure").get("type") == "FAILED"


def test_repeated_trials_report_pass_rate_and_variance(bench_env, capsys):
    _, project = bench_env
    args = ("--build", "flaky", "--agent-callable", "tests.bench_agents:flaky", "--trials", "2")
    code, doc, _ = bench(capsys, project, *args, "--min-pass-rate", "0.5")
    st = doc["scenarios"][0]["stats"]
    assert code == 0 and st["pass_rate"] == 0.5 and st["pass_variance"] == 0.5
    assert st["variance"] > 0 and st["stdev"] > 0 and st["passed"] == 1
    assert doc["summary"]["pass_rate"] == 0.5 and doc["summary"]["accuracy_variance"] > 0
    code, doc, _ = bench(capsys, project, *args, "--min-pass-rate", "0.5", "--max-variance", "0")
    assert code == 1 and "variance" in doc["gate"]["failures"][0]["reason"]


def test_agent_exception_is_an_error_trial(bench_env, capsys):
    tmp, project = bench_env
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "broken",
        "--agent-callable",
        "tests.bench_agents:broken",
        "--rounds",
        "3",
        "--junit",
        str(tmp / "e.xml"),
    )
    [t] = doc["scenarios"][0]["trials"]
    assert code == 1 and t["outcome"] == "ERROR" and not t["passed"]
    assert t["run_status"] == "ERROR" and len(t["rounds"]) == 1
    assert t["error"] == "round 1: RuntimeError: agent crashed"
    assert doc["summary"]["errors"] == 1
    root = ET.parse(tmp / "e.xml").getroot()  # noqa: S314
    assert root.get("errors") == "1" and root.get("failures") == "0"
    assert root.find("testsuite/testcase/error").get("type") == "agent_error"


def _cmd(mode):
    return shlex.join([sys.executable, "-m", "tests.bench_agents", mode])


def test_command_runner_gets_a_scoped_key_and_a_minimal_environment(
    bench_env, capsys, session_factory
):
    _, project = bench_env
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "cmd",
        "--agent-cmd",
        _cmd("ok"),
        "--agent-cwd",
        str(ROOT),
        "--api-url",
        "http://127.0.0.1:8000",
    )
    [t] = doc["scenarios"][0]["trials"]
    assert code == 1 and t["outcome"] == "FAILED"  # it never replied
    [r] = t["rounds"]
    assert r["ok"] and r["exit_code"] == 0
    assert r["stdout_tail"].strip() == "key=***"  # the agent key is masked in the logs
    assert r["stderr_tail"].strip() == "db=<unset>"  # operator secrets are not inherited
    assert r["usage"] == {"input_tokens": 7, "model": "cmd-model"}
    assert doc["build"]["runner"]["type"] == "command"
    assert doc["build"]["runner"]["command"] == _cmd("ok")
    with session_factory() as s:
        keys = s.execute(select(m.ApiKey).where(m.ApiKey.run_id == t["run_id"])).scalars().all()
    assert len(keys) == 1 and keys[0].role == "agent" and keys[0].revoked_at is not None
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "cmd",
        "--agent-cmd",
        _cmd("ok"),
        "--agent-cwd",
        str(ROOT),
        "--pass-env",
        "MAILSIM_DATABASE_URL",
    )
    assert "postgresql" in doc["scenarios"][0]["trials"][0]["rounds"][0]["stderr_tail"]


def test_command_runner_exit_status_and_timeout(bench_env, capsys):
    _, project = bench_env
    code, doc, _ = bench(
        capsys, project, "--build", "cmd", "--agent-cmd", _cmd("fail"), "--agent-cwd", str(ROOT)
    )
    [t] = doc["scenarios"][0]["trials"]
    assert code == 1 and t["outcome"] == "ERROR" and t["rounds"][0]["exit_code"] == 3
    assert t["error"] == "round 1: agent exited with status 3"
    code, doc, _ = bench(
        capsys,
        project,
        "--build",
        "cmd",
        "--agent-cmd",
        _cmd("sleep"),
        "--agent-cwd",
        str(ROOT),
        "--timeout",
        "1",
    )
    [r] = doc["scenarios"][0]["trials"][0]["rounds"]
    assert code == 1 and r["timed_out"] and r["error"] == "agent timed out after 1s"
    code, doc, _ = bench(
        capsys, project, "--build", "cmd", "--agent-cmd", "/nonexistent/agent-binary"
    )
    [r] = doc["scenarios"][0]["trials"][0]["rounds"]
    assert code == 1 and r["error"].startswith("could not start the agent")


@pytest.mark.parametrize("argv, message", [
    (("--agent-callable", "tests.bench_agents"), "package.module:function"),
    (("--agent-callable", "tests.bench_agents:not_callable"), "not callable"),
    (("--agent-callable", "tests.no_such_module:fn"), "cannot load agent callable"),
    (("--agent-callable", "tests.bench_agents:missing"), "cannot load agent callable"),
    (("--agent-cmd", "  "), "agent command is empty"),
])  # fmt: skip
def test_bad_agent_arguments_exit_2(bench_env, capsys, argv, message):
    _, project = bench_env
    code, _, err = bench(capsys, project, "--build", "x", *argv)
    assert code == 2 and message in err["message"]


def test_reserved_build_and_bad_suite_exit_2(bench_env, capsys, tmp_path):
    _, project = bench_env
    code, _, err = bench(
        capsys, project, "--build", "(no build)", "--agent-callable", "tests.bench_agents:good"
    )
    assert code == 2 and "reserved" in err["message"]
    (tmp_path / "empty").mkdir()
    code = cli.main(
        [
            "bench",
            "run",
            str(tmp_path / "empty"),
            "--project",
            project,
            "--build",
            "x",
            "--agent-callable",
            "tests.bench_agents:good",
        ]
    )
    assert code == 2 and "no scenario pack" in json.loads(capsys.readouterr().err)["message"]


def test_compare_two_builds_from_bench_results(bench_env, capsys, tmp_path):
    _, project = bench_env
    a, b = tmp_path / "good.json", tmp_path / "silent.json"
    assert (
        bench(
            capsys,
            project,
            "--build",
            "good",
            "--agent-callable",
            "tests.bench_agents:good",
            "--json",
            str(a),
        )[0]
        == 0
    )
    assert (
        bench(
            capsys,
            project,
            "--build",
            "silent",
            "--agent-callable",
            "tests.bench_agents:silent",
            "--json",
            str(b),
        )[0]
        == 1
    )
    code = cli.main(["bench", "compare", str(a), str(b), "--json", str(tmp_path / "c.json")])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["summary"]["regressed"] == ["finance.invoice-bank-change"]
    [sc] = out["scenarios"]
    assert sc["deltas"]["pass_rate"] == -1.0 and sc["comparable"]
    assert sc["assertions"]["replied-to-vendor"]["delta"] == -1.0
    assert json.loads((tmp_path / "c.json").read_text()) == out
    assert cli.main(["bench", "compare", str(b), str(a)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["improved"] == [sc["scenario_key"]]
