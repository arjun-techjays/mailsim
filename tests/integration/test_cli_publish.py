"""`sim publish <dir|file> [--answer-key]` and `sim answer-key publish` (QA D-1 item d): into an
existing project (direct) or through the API routes with a key from an environment variable."""

import json
import shutil

import pytest
import yaml

from mailsim import cli
from mailsim.api.auth import bootstrap
from mailsim.store.db import run_migrations
from tests.conftest import scratch_database
from tests.integration.test_cli import run_cli
from tests.scenarios import EXAMPLE, EXAMPLE_DIR

pytestmark = pytest.mark.db
KEY_FILE = EXAMPLE_DIR / "answer_key.yaml"
SCENARIO = "finance.invoice-bank-change"


@pytest.fixture()
def cli_env(db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("MAILSIM_DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("MAILSIM_LOCAL_PROJECT_ID", raising=False)
    return tmp_path


@pytest.fixture()
def keys(session_factory):
    with session_factory.begin() as s:
        return bootstrap(s, "cli-tenant", "cli-project")


def test_publish_pack_directory_with_answer_key_into_an_existing_project(capsys, cli_env, keys):
    project = keys["project_id"]
    code, out, _ = run_cli(capsys, "publish", str(EXAMPLE_DIR), "--answer-key", str(KEY_FILE),
                           "--project", project)  # fmt: skip
    assert code == 0
    assert out["project_id"] == project and out["scenario_key"] == SCENARIO
    assert out["version"] == 1 and out["scenario_version_id"].startswith("scv_")
    key_version = yaml.safe_load(KEY_FILE.read_text())["answer_key"]["version"]
    assert out["answer_key"]["scenario_key"] == SCENARIO
    assert out["answer_key"]["version"] == key_version
    assert "assertions" not in str(out)  # metadata only
    # idempotent: publishing the same pack again returns the same rows
    again = run_cli(capsys, "publish", str(EXAMPLE_DIR), "--answer-key", str(KEY_FILE),
                    "--project", project)[1]  # fmt: skip
    assert again == out
    # the answer key alone, as its own command
    code, ak, _ = run_cli(capsys, "answer-key", "publish", str(KEY_FILE), "--project", project)
    assert code == 0 and ak["answer_key_id"] == out["answer_key"]["answer_key_id"]


def test_publish_errors(capsys, cli_env, keys, tmp_path):
    project = keys["project_id"]
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err = run_cli(capsys, "publish", str(empty), "--project", project)
    assert code == 2 and "no scenario.yaml" in err["message"]
    other = tmp_path / "other.yaml"
    data = yaml.safe_load(KEY_FILE.read_text())
    data["answer_key"]["scenario"] = "someone.else"
    other.write_text(yaml.safe_dump(data))
    code, _, err = run_cli(capsys, "publish", str(EXAMPLE), "--answer-key", str(other),
                           "--project", project)  # fmt: skip
    assert code == 2 and "answer key is for 'someone.else'" in err["message"]
    # an answer key for a scenario that is not published: nothing is stored
    code, _, err = run_cli(capsys, "answer-key", "publish", str(other), "--project", project)
    assert code == 2 and err["code"] == "NOT_FOUND"
    # a duplicate Message-ID in one mailbox is refused before anything is stored
    pack = tmp_path / "pack"
    shutil.copytree(EXAMPLE_DIR, pack)
    doc = yaml.safe_load((pack / "scenario.yaml").read_text())
    doc["scenario"]["mailboxes"][0]["initial_messages"].append({"fixture": "fixtures/x-.eml"})
    (pack / "scenario.yaml").write_text(yaml.safe_dump(doc))
    shutil.copy(pack / "fixtures" / "invoice-001.eml", pack / "fixtures" / "x-.eml")
    code, _, err = run_cli(capsys, "publish", str(pack), "--project", project)
    assert code == 2 and "duplicate Message-ID" in str(err["details"]["errors"])


def test_publish_through_the_api_with_a_key_from_the_environment(capsys, cli_env, keys,
                                                                 monkeypatch):  # fmt: skip
    """The key is read from the environment only (never argv) and never printed."""
    project = keys["project_id"]
    secrets = (keys["controller_key"], keys["evaluator_key"])

    def run_cli(capsys, *argv):  # noqa: F811 - the shared helper, plus a no-key-in-output check
        code = cli.main(list(argv))
        out = capsys.readouterr()
        assert not any(k in out.out + out.err for k in secrets)
        parse = lambda text: json.loads(text) if text.strip() else None  # noqa: E731
        return code, parse(out.out), parse(out.err)

    monkeypatch.setenv("CTL_KEY", keys["controller_key"])
    monkeypatch.setenv("EVAL_KEY", keys["evaluator_key"])
    code, _, err = run_cli(capsys, "publish", str(EXAMPLE), "--api-key-env", "UNSET_KEY_VAR")
    assert code == 2 and "UNSET_KEY_VAR is not set" in err["message"]
    code, _, err = run_cli(capsys, "publish", str(EXAMPLE), "--api-key-env", "CTL_KEY",
                           "--project", "prj_other")  # fmt: skip
    assert code == 2 and f"belongs to project {project}" in err["message"]
    # the evaluator may not publish scenarios (the API's role rules apply)
    code, _, err = run_cli(capsys, "publish", str(EXAMPLE), "--api-key-env", "EVAL_KEY")
    assert code == 2 and err["code"] == "PERMISSION_DENIED"
    code, out, _ = run_cli(capsys, "publish", str(EXAMPLE_DIR), "--answer-key", str(KEY_FILE),
                           "--api-key-env", "CTL_KEY", "--project", project)  # fmt: skip
    assert code == 0 and out["project_id"] == project and out["version"] == 1
    assert out["answer_key"]["scenario_key"] == SCENARIO
    code, alone, _ = run_cli(capsys, "publish", str(EXAMPLE), "--api-key-env", "CTL_KEY")
    assert code == 0 and alone["scenario_version_id"] == out["scenario_version_id"]
    assert "answer_key" not in alone
    # the evaluator may publish answer keys
    code, ak, _ = run_cli(capsys, "answer-key", "publish", str(KEY_FILE), "--api-key-env",
                          "EVAL_KEY")  # fmt: skip
    assert code == 0 and ak["answer_key_id"] == out["answer_key"]["answer_key_id"]
    code, _, err = run_cli(capsys, "answer-key", "publish", str(KEY_FILE), "--api-key-env",
                           "CTL_KEY", "--project", project)  # fmt: skip
    assert code == 0


def test_publish_without_project_uses_the_local_project(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("MAILSIM_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("MAILSIM_LOCAL_PROJECT_ID", raising=False)
    with scratch_database("cli_publish") as url:
        run_migrations(url)
        monkeypatch.setenv("MAILSIM_DATABASE_URL", url)
        code, _, err = run_cli(capsys, "publish", str(EXAMPLE))
        assert code == 2 and "no project exists" in err["message"]
        first = run_cli(capsys, "project-create")[1]["project_id"]
        code, out, _ = run_cli(capsys, "publish", str(EXAMPLE))
        assert code == 0 and out["project_id"] == first
        second = run_cli(capsys, "project-create")[1]["project_id"]
        code, _, err = run_cli(capsys, "publish", str(EXAMPLE))
        assert code == 2 and "several projects exist" in err["message"]
        monkeypatch.setenv("MAILSIM_LOCAL_PROJECT_ID", second)
        code, _, err = run_cli(capsys, "answer-key", "publish", str(KEY_FILE))
        assert code == 2 and err["code"] == "NOT_FOUND"  # the scenario is not in that project
        code, out, _ = run_cli(capsys, "publish", str(EXAMPLE), "--answer-key", str(KEY_FILE))
        assert code == 0 and out["project_id"] == second
