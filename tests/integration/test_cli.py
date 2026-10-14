import json

import pytest

from mailsim import cli
from tests.scenarios import EXAMPLE, write_variant

pytestmark = pytest.mark.db


@pytest.fixture()
def cli_env(db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("MAILSIM_DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))
    return tmp_path


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return (
        code,
        (json.loads(out.out) if out.out.strip() else None),
        (json.loads(out.err) if out.err.strip() else None),
    )


def test_validate_ok_and_error(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "validate", str(EXAMPLE))
    assert code == 0 and out["id"] == "finance.invoice-bank-change"
    bad = write_variant(tmp_path, lambda s: s["agent"].update(mailbox="nobody"))
    code, _, err = run_cli(capsys, "validate", str(bad))
    assert code == 2 and err["code"] == "INVALID_REQUEST" and err["details"]["errors"]


def test_end_to_end_create_execute_reset_replay(capsys, cli_env):
    code, out, _ = run_cli(capsys, "project-create")
    project = out["project_id"]
    code, out, _ = run_cli(capsys, "publish", str(EXAMPLE), "--project", project)
    version = out["scenario_version_id"]
    code, out, _ = run_cli(capsys, "run-create", version, "--project", project)
    run = out["run_id"]
    assert run_cli(capsys, "run-start", run, "--project", project)[1]["status"] == "RUNNING"
    code, out, _ = run_cli(capsys, "run-advance", run, "--seconds", "600", "--project", project)
    assert out["fired"] == ["vendor-follow-up"]
    assert len(run_cli(capsys, "run-state", run, "--project", project)[1]["messages"]) == 3
    assert run_cli(capsys, "run-events", run, "--project", project)[1][0]["type"] == "run.created"
    assert (
        run_cli(capsys, "run-actions", run, "--project", project)[1][0]["operation"] == "create_run"
    )
    code, out, _ = run_cli(capsys, "run-replay", run, "--project", project)
    assert code == 0 and out["matches"] is True
    assert run_cli(capsys, "run-reset", run, "--project", project)[1] == {"epoch": 1}
    assert run_cli(capsys, "run-replay", run, "--project", project)[0] == 0
    assert run_cli(capsys, "run-cancel", run, "--project", project)[1]["status"] == "CANCELLED"


def test_replay_mismatch_exit_code_and_errors(capsys, cli_env):
    _, out, _ = run_cli(capsys, "project-create")
    project = out["project_id"]
    version = run_cli(capsys, "publish", str(EXAMPLE), "--project", project)[1][
        "scenario_version_id"
    ]
    run = run_cli(capsys, "run-create", version, "--project", project)[1]["run_id"]
    for blob in (cli_env / "blobs").rglob("*"):
        if blob.is_file():
            blob.unlink()
    code, out, _ = run_cli(capsys, "run-replay", run, "--project", project)
    assert code == 3 and out["missing_blobs"]
    code, _, err = run_cli(capsys, "run-state", "run_missing", "--project", project)
    assert code == 2 and err["code"] == "NOT_FOUND"
    code, _, err = run_cli(capsys, "run-start", run, "--project", "prj_missing")
    assert code == 2 and "not found" in err["message"]


def test_db_upgrade_is_idempotent(capsys, cli_env):
    code, out, _ = run_cli(capsys, "db-upgrade")
    assert code == 0 and out == {"ok": True}
