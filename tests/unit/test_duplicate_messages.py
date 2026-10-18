"""A mailbox never starts with the same message twice (QA D-1): validate and publish refuse a
fixture listed twice in one mailbox and two fixtures with the same Message-ID."""

from pathlib import Path

import pytest
import yaml

from mailsim.domain.errors import InvalidRequest
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.validator import validate_scenario
from tests.scenarios import ROOT, example_dict, write_variant


def _fixture(path: Path, message_id: str | None, subject: str = "Hello") -> None:
    mid = f"Message-ID: {message_id}\r\n" if message_id else ""
    path.write_bytes(
        f"From: vendor@example.test\r\nTo: finance@example.test\r\n{mid}Subject: {subject}\r\n"
        "Date: Mon, 1 Jun 2026 09:00:00 +0000\r\n\r\nBody\r\n".encode()
    )


def _errors(path: Path) -> list[str]:
    return [str(e) for e in validate_scenario(yaml.safe_load(path.read_text()), path.parent).errors]


def test_same_message_id_in_one_mailbox_is_refused(tmp_path):
    def mutate(s):
        s["mailboxes"][0]["initial_messages"].append({"fixture": "fixtures/copy-.eml"})

    path = write_variant(tmp_path, mutate)
    _fixture(path.parent / "fixtures" / "copy-.eml", "<invoice-001@example.test>")
    errors = _errors(path)
    assert errors == [
        "scenario.mailboxes[0].initial_messages[2].fixture: duplicate Message-ID "
        "<invoice-001@example.test> in the mailbox of 'finance_manager': fixture "
        "fixtures/copy-.eml has the same Message-ID as fixtures/invoice-001.eml "
        "(initial_messages.0)"
    ]
    with pytest.raises(InvalidRequest) as exc:  # publish (the loader) refuses it too
        load_scenario(path)
    assert (
        exc.value.details["issues"][0]["path"] == "scenario.mailboxes.0.initial_messages.2.fixture"
    )


def test_fixture_listed_twice_in_one_mailbox_is_refused(tmp_path):
    def mutate(s):
        s["mailboxes"][0]["initial_messages"].append({"fixture": "fixtures/invoice-001.eml"})

    errors = _errors(write_variant(tmp_path, mutate))
    assert errors == [
        "scenario.mailboxes[0].initial_messages[2].fixture: fixture fixtures/invoice-001.eml is "
        "listed more than once in the mailbox of 'finance_manager' (also initial_messages.0)"
    ]


def test_same_message_in_several_mailboxes_and_messages_without_id_are_fine(tmp_path):
    def mutate(s):
        s["mailboxes"][1]["initial_messages"] = [{"fixture": "fixtures/invoice-001.eml"}]
        s["mailboxes"][0]["initial_messages"] += [
            {"fixture": "fixtures/no-id-1.eml"}, {"fixture": "fixtures/no-id-2.eml"}]  # fmt: skip

    path = write_variant(tmp_path, mutate)
    _fixture(path.parent / "fixtures" / "no-id-1.eml", None)
    _fixture(path.parent / "fixtures" / "no-id-2.eml", None)
    assert _errors(path) == []


def test_unreadable_fixtures_are_reported_once_not_as_duplicates(tmp_path):
    def mutate(s):
        s["mailboxes"][0]["initial_messages"].append({"fixture": "fixtures/missing.eml"})

    errors = _errors(write_variant(tmp_path, mutate))
    assert errors == [
        "scenario.mailboxes[0].initial_messages[2].fixture: fixture not found: fixtures/missing.eml"
    ]


def test_without_fixture_directory_duplicates_are_not_checked():
    data = example_dict()
    data["scenario"]["mailboxes"][0]["initial_messages"].append(
        {"fixture": "fixtures/invoice-001.eml"}
    )
    assert validate_scenario(data).errors == []


@pytest.mark.parametrize("path", sorted((ROOT / "scenarios").rglob("scenario.yaml")),
                         ids=lambda p: str(p.parent.relative_to(ROOT)))  # fmt: skip
def test_every_bundled_scenario_still_validates(path):
    assert load_scenario(path).report.ok
