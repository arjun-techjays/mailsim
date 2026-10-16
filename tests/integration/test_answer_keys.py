"""Answer keys: separate versioning, inline split, leak guard, evaluator-only access,
re-evaluation inputs (state_at), and provider identity projections."""

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mailsim.domain.errors import Conflict, InvalidRequest, NotFound
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.providers.ids import gmail_id, graph_id
from mailsim.scenario.answer_key import AnswerKeyDoc
from mailsim.scenario.answer_keys import (
    answer_key_for,
    create_answer_key_version,
    list_answer_keys,
    load_answer_key,
)
from tests.scenarios import EXAMPLE_DIR, write_variant

pytestmark = pytest.mark.db
KEY = "finance.invoice-bank-change"


def publish_key(env, doc):
    with env.session_factory.begin() as s:
        return create_answer_key_version(s, env.scope, doc, env.blobs)


def key_doc(**body):
    base = {"scenario": KEY, "version": 3, "assertions": [
        {"id": "replied", "type": "action", "requirement": "Agent replies to the vendor",
         "check": {"operation": "reply"}}]}  # fmt: skip
    base.update(body)
    return AnswerKeyDoc.model_validate({"answer_key_version": "1.0", "answer_key": base})


def test_inline_assertions_become_a_hidden_versioned_key(env, tmp_path):
    env.version()
    with env.session_factory() as s:
        keys = list_answer_keys(s, env.scope, KEY)
    assert [(k.version, k.source, k.scenario_versions) for k in keys] == [(1, "inline", [1])]
    env.version()  # idempotent

    # editing only the inline assertions makes a NEW KEY version, not a new scenario version
    def tweak(s):
        s["evaluation"]["assertions"][0]["requirement"] = (
            "Agent must never claim the invoice is paid"
        )

    env.version(write_variant(tmp_path, tweak))
    with env.session_factory() as s:
        keys = list_answer_keys(s, env.scope, KEY)
    assert [k.version for k in keys] == [1, 2]
    env.version(write_variant(tmp_path, tweak))
    with env.session_factory() as s:
        assert len(list_answer_keys(s, env.scope, KEY)) == 2


def test_file_keys_compatibility_and_selection(env):
    env.version()
    file_key = publish_key(env, load_answer_key(EXAMPLE_DIR / "answer_key.yaml"))
    assert file_key.version == 3 and file_key.source == "file"
    assert publish_key(env, load_answer_key(EXAMPLE_DIR / "answer_key.yaml")).id == file_key.id
    publish_key(env, key_doc(version=4, scenario_versions=None))
    with env.session_factory() as s:
        assert answer_key_for(s, env.scope, KEY, 1, None).version == 4
        assert answer_key_for(s, env.scope, KEY, 1, 3).version == 3
        assert answer_key_for(s, env.scope, KEY, 9, None).version == 4  # all-versions key
        with pytest.raises(NotFound):
            answer_key_for(s, env.scope, KEY, 9, 3)
    with pytest.raises(Conflict, match="different content"):
        publish_key(env, key_doc(version=4, checks_default="off"))
    publish_key(env, key_doc(version=7, scenario_versions=None))
    with pytest.raises(Conflict, match="older"):
        publish_key(env, key_doc(version=6))
    with pytest.raises(InvalidRequest, match="unknown scenario versions"):
        publish_key(env, key_doc(version=5, scenario_versions=[7]))
    with pytest.raises(NotFound):
        publish_key(env, key_doc(scenario="no.such.scenario"))


def test_answer_key_is_immutable(env, session_factory):
    env.version()
    with session_factory.begin() as s, pytest.raises(DBAPIError):
        s.execute(text("UPDATE answer_keys SET source='x'"))


def test_leak_guard_on_publish(env, tmp_path):
    env.version()
    leaky = key_doc(
        version=5,
        assertions=[
            {
                "id": "x",
                "type": "semantic",
                "requirement": "Please confirm the payment date",
                "rubric": "tone",
            }
        ],
    )  # fmt: skip  (text that is in the invoice fixture)
    with pytest.raises(InvalidRequest, match="agent-visible"):
        publish_key(env, leaky)


def test_inline_leak_is_a_validation_error(tmp_path):
    from mailsim.scenario.loader import load_scenario

    def tweak(s):
        s["evaluation"]["assertions"][0]["requirement"] = "Please use the new bank account"

    with pytest.raises(InvalidRequest) as ei:
        load_scenario(write_variant(tmp_path, tweak))
    assert "leaks" in " ".join(ei.value.details["errors"])


def test_inline_leak_into_fixture_is_detected(tmp_path):
    from mailsim.scenario.loader import load_scenario

    def tweak(s):
        s["evaluation"]["assertions"][0]["requirement"] = (
            "Purchase order PO-001 matches the invoice"
        )

    with pytest.raises(InvalidRequest) as ei:
        load_scenario(write_variant(tmp_path, tweak))
    assert "fixture" in " ".join(ei.value.details["errors"])


def test_load_answer_key_errors(tmp_path):
    with pytest.raises(InvalidRequest, match="not found"):
        load_answer_key(tmp_path / "nope.yaml")
    bad = tmp_path / "k.yaml"
    bad.write_text(yaml.safe_dump({"answer_key_version": "1.0", "answer_key": {"scenario": "s"}}))
    with pytest.raises(InvalidRequest, match="validation failed"):
        load_answer_key(bad)


def test_evaluator_only_access_and_nothing_reaches_the_agent(env):
    version = env.version()
    publish_key(env, load_answer_key(EXAMPLE_DIR / "answer_key.yaml"))
    ctl = Controller(env.engine, env.scope)
    run = ctl.create_run(version)
    ctl.start_run(run)
    ev = EvaluatorView(env.engine, env.scope)
    key = ev.answer_key(run)
    assert key["version"] == 3 and key["source"] == "file"
    assert ev.answer_key(run, version=1)["source"] == "inline"
    assert ev.scenario_spec(run)["spec"]["scenario"]["evaluation"]["assertions"] == []
    # the agent's whole visible world contains no answer-key text
    doc = AnswerKeyDoc.model_validate(key["spec"])
    secrets = [x.requirement for x in doc.answer_key.assertions] + [
        x.id for x in doc.answer_key.assertions if len(x.id) > 8
    ]
    agent = AgentMailbox(env.engine, env.scope, run, ctl.mailbox_id_for(run, "finance_manager"))
    first = agent.list_messages().items[0]
    agent.reply(first.id, text="We will verify this.")
    ctl.advance(run, 700)
    visible = [m.model_dump_json() for m in agent.list_messages(page_size=500).items]
    visible += [
        env.blobs.get(m.raw_blob_ref).decode("utf-8", "replace")
        for m in agent.list_messages(page_size=500).items
    ]
    visible += [str(agent.list_folders()), str(agent.list_changes(0))]
    blob = "\n".join(visible).casefold()
    assert not [x for x in secrets if x.casefold() in blob]
    # run config (copied into the event log) holds only the switch, never the key
    assert "assertions" not in str(ev.events(run)[0]["payload"]["config"])


def test_state_at_reconstructs_checkpoints_without_persisting(env):
    run = env.run()
    fm = env.mbx(run, "finance_manager")
    ev = EvaluatorView(env.engine, env.scope)
    seeded_seq = next(e["sequence"] for e in ev.events(run) if e["type"] == "run.seeded")
    env.engine.inject(
        env.scope,
        run,
        fm,
        {
            "from": "vendor@example.test",
            "to": ["finance@example.test"],
            "subject": "later",
            "text": "x",
        },
    )
    at_seed = ev.state_at(run, seeded_seq)
    assert len(at_seed["messages"]) == 2 and len(ev.state(run)["messages"]) == 3
    with pytest.raises(InvalidRequest):
        ev.state_at(run, 0)
    with pytest.raises(InvalidRequest):
        ev.state_at(run, 10_000)
    from mailsim.store import repos

    with env.session_factory() as s:
        assert len(repos.list_runs(s, env.scope)) == 1, "no throw-away run was persisted"


def test_provider_ids_and_imap_uids_are_projected(env):
    run = env.run()
    st = env.engine.state(env.scope, run)
    msgs = {m["id"]: m for m in st["messages"]}
    gmail = {p["entity_id"]: p["external_id"] for p in st["provider_ids"]
             if p["provider"] == "gmail" and p["entity_type"] == "message"}  # fmt: skip
    assert gmail == {mid: gmail_id(mid) for mid in msgs}
    assert all(gid != mid for mid, gid in gmail.items())
    graph = [
        p for p in st["provider_ids"] if p["provider"] == "graph" and p["entity_type"] == "thread"
    ]
    assert graph and graph[0]["external_id"] == graph_id(graph[0]["entity_id"])
    inbox_uids = sorted(m["imap_uid"] for m in st["messages"])
    assert inbox_uids == [1, 2]
    fm = env.mbx(run, "finance_manager")
    first = min(st["messages"], key=lambda m: m["imap_uid"])
    env.engine.move(env.scope, run, fm, first["id"], "archive", actor="controller")
    after = {m["id"]: m for m in env.engine.state(env.scope, run)["messages"]}
    assert after[first["id"]]["imap_uid"] == 1, "first message in Archive gets UID 1 there"
    env.engine.move(env.scope, run, fm, first["id"], "inbox", actor="controller")
    assert {m["id"]: m for m in env.engine.state(env.scope, run)["messages"]}[first["id"]][
        "imap_uid"
    ] == 3
    env.engine.delete(env.scope, run, fm, first["id"], actor="controller")
    st2 = env.engine.state(env.scope, run)
    assert first["id"] not in {p["entity_id"] for p in st2["provider_ids"]}
    assert env.engine.replay_run(env.scope, run).matches
