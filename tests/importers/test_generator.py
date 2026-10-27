"""Leadership generator on a synthetic corpus: validity, volume and topic mix, threading,
pseudonymization, determinism and publishing into the engine."""

from __future__ import annotations

import email
from email import policy
from pathlib import Path

import pytest

from mailsim.importers import enron, leadership
from mailsim.importers.leadership import GenerateOptions, generate, write_scenarios
from mailsim.importers.pii import Scanner
from mailsim.scenario.loader import load_scenario
from tests.importers.conftest import PEOPLE, TEST_KEY

VOLUME = {"ceo": 14, "cfo": 12, "cbo": 12, "director_finance": 10}


@pytest.fixture(scope="module")
def corpus(shards):
    records, header, registry = enron.import_enron(
        shards, enron.ImportConfig(limit=1000, seed=7, id_key=TEST_KEY)
    )
    return header, records, registry


def _opts(**kw) -> GenerateOptions:
    base = {"seed": 11, "volume": dict(VOLUME)}
    base.update(kw)
    return GenerateOptions(**base)


@pytest.fixture(scope="module")
def generated(corpus, tmp_path_factory):
    header, records, registry = corpus
    scenarios = generate(header, records, _opts(extra_names=registry.real_names()))
    out = tmp_path_factory.mktemp("leadership")
    write_scenarios(out, scenarios)
    return out, scenarios


def _messages(root: Path) -> list[email.message.EmailMessage]:
    return [email.message_from_bytes(p.read_bytes(), policy=policy.default)  # type: ignore[misc]
            for p in sorted((root / "fixtures").glob("*.eml"))]  # fmt: skip


def test_one_valid_scenario_per_target_inbox(generated):
    out, scenarios = generated
    assert [s.role for s in scenarios] == ["ceo", "cfo", "cbo", "director_finance"]
    for sc in scenarios:
        loaded = load_scenario(out / sc.directory / "scenario.yaml")
        body = loaded.doc.scenario
        assert body.id == sc.scenario_id and body.agent.mailbox == sc.role
        seeded = [mb for mb in body.mailboxes if mb.initial_messages]
        assert [mb.owner for mb in seeded] == [sc.role]
        assert {mb.owner for mb in body.mailboxes} == {p.id for p in body.personas}


def test_volume_topic_mix_and_risk_items(generated):
    out, scenarios = generated
    for sc in scenarios:
        assert sc.messages == VOLUME[sc.role], (sc.role, sc.messages, sc.skipped)
        mix = leadership.TOPIC_MIX[sc.role]
        top = max(mix, key=lambda t: mix[t])
        assert max(sc.topics, key=lambda t: sc.topics[t]) == top, sc.topics
        subjects = [m["Subject"] for m in _messages(out / sc.directory)]
        risks = leadership.RISKS[sc.role]
        assert all(r.subject in subjects for r in risks)
        assert sc.threads < sc.messages  # quoted chains became threads


def test_every_message_targets_the_inbox_owner_and_threads_resolve(generated):
    out, scenarios = generated
    for sc in scenarios:
        owner = leadership.BY_ID[sc.role].email
        msgs = _messages(out / sc.directory)
        ids = {m["Message-ID"] for m in msgs}
        dates = [m["Date"].datetime for m in msgs]
        assert dates == sorted(dates)
        for m in msgs:
            rcpts = (m.get("To", "") + "," + m.get("Cc", "")).lower()
            assert owner in rcpts
            if m["In-Reply-To"]:
                assert m["In-Reply-To"] in ids
                assert set(m["References"].split()) <= ids
                assert m["Subject"].startswith(("RE: ", "FW: "))


def test_no_real_identifiers_in_generated_output(generated, corpus):
    out, _ = generated
    registry = corpus[2]
    findings = Scanner(registry.real_names()).scan_paths([out])
    assert findings == []
    text = "\n".join(p.read_text(errors="replace") for p in out.rglob("*.eml"))
    for first, last in PEOPLE:
        assert f"{first} {last}" not in text and last not in text
    assert "@enron" not in text.lower() and "/HOU/" not in text


def test_same_seed_is_byte_identical_and_other_seed_differs(corpus):
    header, records, registry = corpus
    a = generate(header, records, _opts())
    b = generate(header, records, _opts())
    assert [s.files for s in a] == [s.files for s in b]
    c = generate(header, records, _opts(seed=12))
    assert [s.files for s in a] != [s.files for s in c]


def test_published_mailbox_has_the_fixtures_and_linked_threads(env, generated):
    out, scenarios = generated
    sc = scenarios[0]
    run = env.run(out / sc.directory / "scenario.yaml")
    mbx = env.mbx(run, sc.role)
    state = env.engine.state(env.scope, run)
    mine = [m for m in state["messages"] if m["mailbox_id"] == mbx]
    assert len(mine) == sc.messages
    by_header = {m["message_id_header"]: m for m in mine}
    replies = [m for m in mine if m["in_reply_to"]]
    assert replies
    for m in replies:
        parent = by_header[m["in_reply_to"]]
        assert parent["thread_id"] == m["thread_id"]
    assert len({m["thread_id"] for m in mine}) == sc.threads


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_options_and_errors(corpus):
    header, records, _ = corpus
    with pytest.raises(ValueError, match="unknown role"):
        generate(header, records, _opts(roles=["janitor"]))
    with pytest.raises(ValueError, match="collide"):
        generate(header, records, _opts(extra_names=["Helena Marsh"]))
    other = generate(header, records, _opts(roles=["coo", "executive_assistant"],
                                            volume={"coo": 6, "executive_assistant": 5}))  # fmt: skip
    assert [s.messages for s in other] == [6, 5]
    assert not leadership.RISKS.get("coo")
