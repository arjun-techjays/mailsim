"""Agent mailbox routes: bound to one run + mailbox, agent guards and budget enforced."""

from __future__ import annotations

import pytest

from tests.api.conftest import open_agent

pytestmark = pytest.mark.db


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def agent(api):
    api.publish(open_agent)
    created = api.create_run()
    key, mbx = api.agent(created)
    return created["run"]["id"], mbx, key


def items(api, url, key):
    return api.call("GET", url, key=key, expect=200).json()["items"]


def test_list_get_thread_folders(api, agent):
    rid, mbx, key = agent
    inbox = items(api, f"/v1/mailboxes/{mbx}/messages?folder=inbox", key)
    assert len(inbox) == 2 and all(m["folder"] == "INBOX" for m in inbox)
    assert items(api, f"/v1/mailboxes/{mbx}/messages?folder=Sent", key) == []
    msg = api.call("GET", f"/v1/mailboxes/{mbx}/messages/{inbox[0]['id']}", key=key, expect=200)
    assert msg.json()["from"]["email"] == "vendor@example.test"
    thread = api.call("GET", f"/v1/mailboxes/{mbx}/threads/{inbox[0]['thread_id']}", key=key,
                      expect=200).json()  # fmt: skip
    assert inbox[0]["id"] in [t["id"] for t in thread]
    folders = api.call("GET", f"/v1/mailboxes/{mbx}/folders", key=key, expect=200).json()
    assert [f["system_type"] for f in folders] == ["inbox", "sent", "drafts", "trash", "archive"]
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages/msg_nope", key=key), 404, "NOT_FOUND")
    err(api.call("GET", f"/v1/mailboxes/{mbx}/threads/thr_nope", key=key), 404, "NOT_FOUND")
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages?folder=nope", key=key), 404, "NOT_FOUND")
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages?cursor=@@", key=key), 400,
        "INVALID_REQUEST")  # fmt: skip


def test_search_with_folder_and_pagination(api, agent):
    rid, mbx, key = agent
    for i in range(5):
        api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", expect=201, json={"message": {
            "from": "vendor@example.test", "to": ["finance@example.test"],
            "subject": f"needle {i}", "text": "t"}})  # fmt: skip
    api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key, expect=201,
             json={"to": ["vendor@example.test"], "subject": "needle sent", "text": "x"})  # fmt: skip
    every = items(api, f"/v1/mailboxes/{mbx}/messages?q=needle&page_size=500", key)
    assert len(every) == 6
    seen, cursor = [], None
    while True:
        url = f"/v1/mailboxes/{mbx}/messages?q=needle&page_size=2"
        page = api.call("GET", url + (f"&cursor={cursor}" if cursor else ""), key=key,
                        expect=200).json()  # fmt: skip
        seen += [m["id"] for m in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [m["id"] for m in every] and len(set(seen)) == 6
    sent = items(api, f"/v1/mailboxes/{mbx}/messages?q=needle&folder=sent", key)
    assert [m["subject"] for m in sent] == ["needle sent"]
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages?q=needle&folder=nope", key=key), 404,
        "NOT_FOUND")  # fmt: skip
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages?q=%20", key=key), 400, "INVALID_REQUEST")
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages?q=x&page_size=501", key=key), 400,
        "INVALID_REQUEST")  # fmt: skip
    # plain listing pages are complete too
    seen, cursor = [], None
    while True:
        url = f"/v1/mailboxes/{mbx}/messages?page_size=3"
        page = api.call("GET", url + (f"&cursor={cursor}" if cursor else ""), key=key,
                        expect=200).json()  # fmt: skip
        seen += [m["id"] for m in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert (
        len(seen)
        == len(set(seen))
        == len(items(api, f"/v1/mailboxes/{mbx}/messages?page_size=500", key))
    )


def test_reply_send_patch_delete_changes(api, agent):
    rid, mbx, key = agent
    first = items(api, f"/v1/mailboxes/{mbx}/messages?folder=inbox", key)[0]
    reply = api.call("POST", f"/v1/mailboxes/{mbx}/messages/{first['id']}/reply", key=key,
                     json={"text": "ack", "reply_all": True}, expect=201).json()  # fmt: skip
    assert set(reply) == {"message_id", "message_id_header"} and reply["message_id_header"]
    sent = api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key, expect=201,
                    json={"to": ["vendor@example.test"], "subject": "s", "text": "t"}).json()  # fmt: skip
    assert set(sent) == {"message_id", "message_id_header"}
    body = err(api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key,
                        json={"to": ["ceo@real-company.example"], "subject": "x"}),
               422, "EXTERNAL_DELIVERY_BLOCKED")  # fmt: skip
    assert body["details"]["recipients"] == ["ceo@real-company.example"]
    _, since = feed(api, mbx, key, 0)
    patched = api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key, expect=200,
                       json={"folder": "archive", "add_labels": ["todo"], "add_flags": ["FLAGGED"]}).json()  # fmt: skip
    assert patched["folder"] == "Archive" and "todo" in patched["labels"]
    assert "FLAGGED" in patched["flags"]
    patched = api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key, expect=200,
                       json={"remove_labels": ["todo"], "remove_flags": ["FLAGGED"]}).json()  # fmt: skip
    assert "todo" not in patched["labels"] and "FLAGGED" not in patched["flags"]
    changed, _ = feed(api, mbx, key, since)
    assert set(changed) == {first["id"]}
    err(api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key, json={}), 400,
        "INVALID_REQUEST")  # fmt: skip
    err(api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key,
                 json={"add_flags": ["BOGUS"]}), 400, "INVALID_REQUEST")  # fmt: skip
    r = api.call("DELETE", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key, expect=204)
    assert r.content == b""
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key), 404, "NOT_FOUND")
    err(api.call("GET", f"/v1/mailboxes/{mbx}/changes?since=-1", key=key), 400, "INVALID_REQUEST")


def feed(api, mbx, key, since):
    ids = []
    while True:
        out = api.call("GET", f"/v1/mailboxes/{mbx}/changes?since={since}&limit=2", key=key,
                       expect=200).json()  # fmt: skip
        ids += [c["message_id"] for c in out["changes"] if c["message_id"]]
        since = out["next_since"]
        if not out["has_more"]:
            return ids, since


def test_agent_key_is_bound_to_its_run_and_mailbox(api, agent):
    rid, mbx, key = agent
    other = api.create_run()
    other_key, other_mbx = api.agent(other)
    assert other_mbx == mbx  # deterministic ids: same mailbox id in another run...
    vendor = api.engine.mailbox_id_for(api_scope(api), rid, "vendor")
    err(api.call("GET", f"/v1/mailboxes/{vendor}/messages", key=key), 403, "PERMISSION_DENIED")
    # ...yet each key only ever sees its own run's state
    api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key, expect=201,
             json={"to": ["finance@example.test"], "subject": "only in run 1", "text": "x"})  # fmt: skip
    mine = items(api, f"/v1/mailboxes/{mbx}/messages?q=only%20in%20run", key)
    theirs = items(api, f"/v1/mailboxes/{mbx}/messages?q=only%20in%20run", other_key)
    assert len(mine) == 2 and theirs == []
    # an agent key never reaches control, simulator or evaluation routes
    for method, url in (("GET", f"/v1/runs/{rid}"), ("GET", f"/v1/runs/{rid}/state"),
                        ("POST", f"/v1/runs/{rid}/reset"), ("GET", f"/v1/runs/{rid}/results"),
                        ("GET", "/v1/scenarios/finance.invoice-bank-change/answer-keys")):  # fmt: skip
        err(api.call(method, url, key=key), 403, "PERMISSION_DENIED")


def api_scope(api):
    from mailsim.store.repos import Scope

    return Scope(api.tenant_id, api.project_id)


def test_operation_policy_budget_and_run_state(api, tmp_path):
    api.publish(lambda s: s["evaluation"]["limits"].update(max_agent_actions=2))
    created = api.create_run()
    rid, (key, mbx) = created["run"]["id"], api.agent(created)
    first = items(api, f"/v1/mailboxes/{mbx}/messages", key)[0]
    # example scenario allows read/search/reply/move only: send is denied (and audited)
    err(api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key,
                 json={"to": ["vendor@example.test"], "subject": "x"}), 403, "PERMISSION_DENIED")  # fmt: skip
    api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key,
             json={"folder": "archive"}, expect=200)  # fmt: skip
    api.call("POST", f"/v1/mailboxes/{mbx}/messages/{first['id']}/reply", key=key,
             json={"text": "verify"}, expect=201)  # fmt: skip
    body = err(api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key,
                        json={"folder": "inbox"}), 429, "QUOTA_EXCEEDED")  # fmt: skip
    assert (body["message"], body["details"]) == ("action limit reached", {})
    for _ in range(3):  # reads stay free after the write budget is spent
        items(api, f"/v1/mailboxes/{mbx}/messages", key)
    api.call("POST", f"/v1/runs/{rid}/reset", expect=200)  # READY again: not active
    body = err(api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key), 409, "RUN_NOT_ACTIVE")
    assert body["message"] == "mailbox is not available"
    api.call("POST", f"/v1/runs/{rid}/cancel", expect=200)  # cancel revokes the agent key
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages", key=key), 401, "UNAUTHENTICATED")


def test_agent_never_sees_answer_key_text(api, agent):
    import yaml

    from tests.scenarios import EXAMPLE_DIR

    rid, mbx, key = agent
    api.call("POST", "/v1/scenarios/finance.invoice-bank-change/answer-keys",
             json={"source": (EXAMPLE_DIR / "answer_key.yaml").read_text()}, expect=201)  # fmt: skip
    first = items(api, f"/v1/mailboxes/{mbx}/messages", key)[0]
    api.call("POST", f"/v1/mailboxes/{mbx}/messages/{first['id']}/reply", key=key,
             json={"text": "We will verify"}, expect=201)  # fmt: skip
    api.call("POST", f"/v1/runs/{rid}/clock/advance", json={"seconds": 700}, expect=200)
    doc = yaml.safe_load((EXAMPLE_DIR / "answer_key.yaml").read_text())
    secrets = [a["requirement"] for a in doc["answer_key"]["assertions"]]
    secrets += [a["requirement"] for a in yaml.safe_load(
        (EXAMPLE_DIR / "scenario.yaml").read_text())["scenario"]["evaluation"]["assertions"]]  # fmt: skip
    seen = ""
    for url in (f"/v1/mailboxes/{mbx}/messages?page_size=500", f"/v1/mailboxes/{mbx}/folders",
                f"/v1/mailboxes/{mbx}/changes"):  # fmt: skip
        seen += api.call("GET", url, key=key, expect=200).text
    assert "message" in seen and not [s for s in secrets if s in seen]


def test_received_mail_cannot_be_patched_into_sent_or_drafts(api, agent):
    """QA B-5: a PATCH folder change must not forge "sent"/"drafted" evidence."""
    rid, mbx, key = agent
    first = items(api, f"/v1/mailboxes/{mbx}/messages?folder=inbox", key)[0]
    for folder in ("Sent", "drafts"):
        r = api.call("PATCH", f"/v1/mailboxes/{mbx}/messages/{first['id']}", key=key,
                     json={"folder": folder})  # fmt: skip
        assert "cannot be placed" in err(r, 403, "PERMISSION_DENIED")["message"]
    assert items(api, f"/v1/mailboxes/{mbx}/messages?folder=sent", key) == []


def test_system_labels_and_the_draft_flag_are_not_agent_writable(api, agent):
    """QA D-5: SENT / DRAFT / INBOX ... are derived (folder, flags, real sends and drafts), so a
    PATCH can't add or remove them; refused before the first write (the move is not applied)."""
    rid, mbx, key = agent
    first = items(api, f"/v1/mailboxes/{mbx}/messages?folder=inbox", key)[0]
    url = f"/v1/mailboxes/{mbx}/messages/{first['id']}"
    for labels in (["SENT"], ["Draft"], ["ok", "inbox"]):
        for field in ("add_labels", "remove_labels"):
            r = api.call("PATCH", url, key=key, json={"folder": "archive", field: labels})
            assert "system labels" in err(r, 400, "INVALID_REQUEST")["message"]
    r = api.call("PATCH", url, key=key, json={"add_flags": ["DRAFT"]})
    assert "marks drafts only" in err(r, 403, "PERMISSION_DENIED")["message"]
    now = api.call("GET", url, key=key, expect=200).json()
    assert now["folder"] == "INBOX" and now["labels"] == ["INBOX", "UNREAD"] and now["flags"] == []
    assert items(api, f"/v1/mailboxes/{mbx}/messages?folder=sent", key) == []
