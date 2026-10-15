"""Operator (controller/evaluator) reads: typed mailbox inspection and filtered event pages."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_inspector_mailbox_reads_record_nothing_and_work_after_completion(api):
    api.publish()
    created = api.create_run()
    rid = created["run"]["id"]
    boxes = api.call("GET", f"/v1/runs/{rid}/mailboxes", expect=200).json()
    assert {b["owner_id"] for b in boxes} == {"finance_manager", "vendor"}
    fm = next(b["id"] for b in boxes if b["owner_id"] == "finance_manager")
    before = api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json()
    page = api.call("GET", f"/v1/runs/{rid}/mailboxes/{fm}/messages?page_size=1",
                    role="evaluator", expect=200).json()  # fmt: skip
    assert len(page["items"]) == 1 and page["next_cursor"]
    rest = api.call("GET", f"/v1/runs/{rid}/mailboxes/{fm}/messages?page_size=50&order=desc",
                    expect=200).json()  # fmt: skip
    assert (
        len(rest["items"]) == 2 and rest["items"][0]["created_at"] >= rest["items"][1]["created_at"]
    )
    inbox = api.call("GET", f"/v1/runs/{rid}/mailboxes/{fm}/messages?folder=inbox", expect=200)
    assert inbox.json()["items"]
    assert api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json() == before, "no actions"
    api.call("POST", f"/v1/runs/{rid}/cancel", expect=200)
    assert api.call("GET", f"/v1/runs/{rid}/mailboxes/{fm}/messages", expect=200).json()["items"]
    r = api.call("GET", f"/v1/runs/{rid}/mailboxes/mbx_nope/messages")
    assert r.status_code == 404
    r = api.call("GET", f"/v1/runs/{rid}/mailboxes/{fm}/messages?cursor=@@@")
    assert r.status_code == 400


def test_event_pages_through_the_facade(api):
    api.publish()
    rid = api.create_run()["run"]["id"]
    seen, cursor = [], None
    while True:
        q = f"/v1/runs/{rid}/events?page_size=3&type=persona.created" + (
            f"&cursor={cursor}" if cursor else ""
        )
        page = api.call("GET", q, expect=200).json()
        seen += [e["sequence"] for e in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 2 and seen == sorted(seen)
