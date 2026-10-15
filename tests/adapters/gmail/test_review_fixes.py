"""Regression tests for the WS-C review: ReDoS, label registry, replies under the shipped
permissions, no-op trash, auth-first catch-all and input bounds."""

from __future__ import annotations

import json
import time

import pytest

from mailsim.adapters.gmail import labels as lb
from mailsim.adapters.gmail.fields import parse_fields
from mailsim.adapters.gmail.resources import html_to_text
from mailsim.adapters.gmail.search import parse_query
from mailsim.domain.errors import InvalidRequest
from tests.adapters.gmail.conftest import BASE, INVOICE, PO, Gm, b64, raw_message

# ------------------------------------------------------------------------------ B1 ReDoS


@pytest.mark.parametrize("markup", ["<script" * 200_000, "<" * 1_000_000, "<p" * 300_000,
                                    "<style>" + "x" * 1_000_000, "<a " + "b" * 2_000_000])  # fmt: skip
def test_html_to_text_is_linear(markup):
    started = time.perf_counter()
    html_to_text(markup)
    assert time.perf_counter() - started < 0.5


def test_html_to_text_output():
    assert (
        html_to_text("a<script>x<b></script >b<br/>c&amp;<style>s</style>d</p>e") == "a b\nc& d\ne"
    )
    assert html_to_text("a<script>never closed") == "a"
    assert html_to_text("a<b unterminated") == "a"
    assert html_to_text("x" * 10, limit=4) == "xxxx"


# ------------------------------------------------------------------- B2 label registry


@pytest.mark.db
def test_label_settings_are_validated(gm):
    for body in ({"name": "a", "messageListVisibility": "x"},
                 {"name": "a", "labelListVisibility": "show"},
                 {"name": "a", "color": "#000000"},
                 {"name": "a", "messageListVisibility": ["show"]},
                 {"name": "a", "labelListVisibility": {"x": 1}},
                 {"name": "a", "color": {"textColor": "#000000"}},
                 {"name": "a", "color": {"textColor": "#00000", "backgroundColor": "#ffffff"}},
                 {"name": "a", "color": {"textColor": "#000000", "backgroundColor": "#ffffff",
                                         "extra": "#000000"}}):  # fmt: skip
        r = gm.call("POST", "/labels", 400, json=body)
        assert gm.error(r) == (400, "INVALID_REQUEST", "invalidArgument"), body
    ok = gm.post("/labels", json={"name": "a", "messageListVisibility": "hide",
                                  "labelListVisibility": "labelShowIfUnread",
                                  "color": {"textColor": "#000000", "backgroundColor": "#FFFFFF"},
                                  "ignored": "dropped"})  # fmt: skip
    assert "ignored" not in ok and ok["messageListVisibility"] == "hide"
    r = gm.call("PATCH", f"/labels/{ok['id']}", 400, json={"labelListVisibility": "nope"})
    assert gm.error(r)[1] == "INVALID_REQUEST"


@pytest.mark.db
def test_label_registry_is_capped(gm, monkeypatch):
    monkeypatch.setattr(lb, "MAX_REGISTERED_LABELS", 2)
    gm.post("/labels", json={"name": "one"})
    gm.post("/labels", json={"name": "two"})
    r = gm.call("POST", "/labels", 400, json={"name": "three"})
    assert gm.error(r)[1] == "INVALID_REQUEST"


def test_label_registry_keeps_a_bounded_number_of_buckets(monkeypatch):
    monkeypatch.setattr(lb, "MAX_REGISTRY_KEYS", 2)
    reg = lb.LabelRegistry()
    reg.put(("r1", "m"), "A", {}, 1)
    reg.put(("r2", "m"), "B", {}, 1)
    reg.put(("r1", "m"), "C", {}, 1)  # r1 written most recently
    reg.put(("r3", "m"), "D", {}, 1)  # evicts r2, the least recently written
    assert reg.get(("r2", "m")) == {} and reg.since(("r2", "m")) is None
    assert set(reg.get(("r1", "m"))) == {"A", "C"} and set(reg.get(("r3", "m"))) == {"D"}


@pytest.mark.db
def test_created_labels_do_not_survive_a_reset(gm):
    gm.post("/labels", json={"name": "Temp"})
    assert "Temp" in [x["name"] for x in gm.get("/labels")["labels"]]
    gm.ctl.reset_run(gm.run)
    gm.ctl.start_run(gm.run)
    assert [x for x in gm.get("/labels")["labels"] if x["type"] == "user"] == []
    gm.post("/labels", json={"name": "Temp"})  # can be created again in the new epoch
    assert "Temp" in [x["name"] for x in gm.get("/labels")["labels"]]


# --------------------------------------------- PR #1 replies under shipped permissions


def _shipped(s):  # the example scenario as shipped: read, search, reply, move; no send
    pass


@pytest.mark.db
def test_custom_reply_is_one_reply_under_shipped_permissions(env, tmp_path):
    g = Gm(env, tmp_path, _shipped)
    before = len(g.ctl.actions(g.run))
    raw = raw_message({"To": "vendor@example.test", "Cc": "Finance@Example.test",
                       "Subject": "AW: Invoice INV-001", "In-Reply-To": "<invoice-001@example.test>"},
                      "We will verify the bank details independently.")  # fmt: skip
    sent = g.post("/messages/send", json={"raw": b64(raw)})
    writes = [(a["operation"], a["outcome"]) for a in g.ctl.actions(g.run)[before:]
              if a["operation"] != "read"]  # fmt: skip
    assert writes == [("reply", "ok")]
    assert "MAILSIM_ANSWERED" in g.get(f"/messages/{g.gid(INVOICE)}")["labelIds"]
    assert "event.armed" in {e["type"] for e in g.ev.events(g.run)}  # send_reply trigger
    got = g.get(f"/messages/{sent['id']}", params={"format": "metadata"})
    headers = {h["name"]: h["value"] for h in got["payload"]["headers"]}
    assert headers["Subject"] == "AW: Invoice INV-001" and headers["Cc"] == "finance@example.test"
    assert headers["In-Reply-To"] == "<invoice-001@example.test>"


@pytest.mark.db
def test_reply_target_falls_back_to_older_references(env, tmp_path):
    g = Gm(env, tmp_path, _shipped)
    raw = raw_message({"To": "vendor@example.test", "Subject": "Re: Invoice INV-001",
                       "In-Reply-To": "<not-here@example.test>",
                       "References": "<invoice-001@example.test> <not-here@example.test>"},
                      "Following up.")  # fmt: skip
    g.post("/messages/send", json={"raw": b64(raw)})  # a reply (send is not allowed here)
    assert "MAILSIM_ANSWERED" in g.get(f"/messages/{g.gid(INVOICE)}")["labelIds"]


@pytest.mark.db
def test_reply_with_external_cc_is_blocked_without_leftovers(env, tmp_path):
    g = Gm(env, tmp_path, _shipped)
    raw = raw_message({"To": "vendor@example.test", "Cc": "boss@real-company.example",
                       "Subject": "Re: Invoice INV-001", "In-Reply-To": "<invoice-001@example.test>"})  # fmt: skip
    r = g.call("POST", "/messages/send", 400, json={"raw": b64(raw)})
    assert g.error(r) == (400, "EXTERNAL_DELIVERY_BLOCKED", "failedPrecondition")
    assert g.get("/drafts") == {"resultSizeEstimate": 0}
    assert all(m["folder_id"] for m in g.ev.state(g.run)["messages"])
    assert not [a for a in g.ctl.actions(g.run) if a["operation"] == "draft"]
    assert ("reply", "EXTERNAL_DELIVERY_BLOCKED") in g.actions()
    # a plain send is not permitted by the shipped scenario
    r = g.call("POST", "/messages/send", 403, json={"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "new"}))})  # fmt: skip
    assert g.error(r)[1] == "PERMISSION_DENIED"


# ------------------------------------------------------------- PR #2 no-op trash


@pytest.mark.db
def test_trash_untrash_noops_cost_nothing(gm):
    inv = gm.gid(INVOICE)
    n = len(gm.ctl.actions(gm.run))
    gm.post(f"/messages/{inv}/untrash")  # not in trash
    assert [a for a in gm.ctl.actions(gm.run)[n:] if a["operation"] != "read"] == []
    gm.post(f"/messages/{inv}/trash")
    n = len(gm.ctl.actions(gm.run))
    assert gm.post(f"/messages/{inv}/trash")["labelIds"] == ["TRASH", "UNREAD"]
    thread = gm.get(f"/messages/{inv}", params={"format": "minimal"})["threadId"]
    gm.post(f"/messages/{gm.gid(PO)}/trash")
    m = len(gm.ctl.actions(gm.run))
    gm.post(f"/threads/{thread}/trash")
    assert [a for a in gm.ctl.actions(gm.run)[n:m] if a["operation"] != "read"] == [
        a for a in gm.ctl.actions(gm.run)[n:m] if a["operation"] == "move"]  # fmt: skip
    assert [a for a in gm.ctl.actions(gm.run)[m:] if a["operation"] != "read"] == []


# ----------------------------------------------------------------- S1 .. S3, PR #9


@pytest.mark.db
def test_catch_all_requires_auth_first(gm):
    for path in ("/settings/imap", "/nothing"):
        r = gm.client.get(BASE + path, headers={"Authorization": ""})
        assert r.status_code == 401


@pytest.mark.db
def test_input_bounds(gm):
    for params in ({"q": "a" * 2049}, {"q": "(" * 40 + "a" + ")" * 40}, {"q": "-" * 40 + "a"},
                   {"q": "after:99999999999"}, {"q": "older_than:200y"},
                   {"q": "before:２０２６/01/01"}, {"fields": "a," * 600},
                   {"fields": "a(" * 40 + "b" + ")" * 40}, {"fields": "/".join(["a"] * 40)}):  # fmt: skip
        r = gm.call("GET", "/messages", 400, params=params)
        assert gm.error(r)[1] == "INVALID_REQUEST", params
    for start in ("１２", "9" * 19, "9" * 20, ""):
        r = gm.call("GET", "/history", 400, params={"startHistoryId": start})
        assert gm.error(r)[1] == "INVALID_REQUEST", start
    assert gm.get("/messages", params={"q": "older_than:100y"}) == {"resultSizeEstimate": 0}
    assert len(gm.ids(q="(" * 30 + "invoice" + ")" * 30)) == 2


def _multipart(meta: bytes, raw: bytes) -> tuple[bytes, dict]:
    body = (b"--b\r\nContent-Type: application/json\r\n\r\n" + meta + b"\r\n--b\r\n"
            b"Content-Type: message/rfc822\r\n\r\n" + raw + b"\r\n--b--")  # fmt: skip
    return body, {"Content-Type": 'multipart/related; boundary="b"'}


@pytest.mark.db
def test_upload_metadata_bounds(gm):
    up = "/upload/gmail/v1/users/me"
    raw = raw_message({"To": "vendor@example.test", "Subject": "x"})
    for meta in (json.dumps({"pad": "x" * 70_000}).encode(), b"[" * 100_000 + b"]" * 100_000,
                 b'{"message": [1]}', b'{"message": "x"}'):  # fmt: skip
        body, headers = _multipart(meta, raw)
        for path in ("/drafts", "/drafts/send", "/messages/send"):
            r = gm.call("POST", path, None, base=up, params={"uploadType": "multipart"},
                        content=body, headers=headers)  # fmt: skip
            if path == "/messages/send" and meta.startswith(b'{"message"'):
                continue  # messages.send ignores a `message` key
            assert r.status_code == 400 and gm.error(r)[1] == "INVALID_REQUEST", (path, meta[:20])


@pytest.mark.db
def test_single_folder_filters_are_pushed_down(gm):
    expected = [gm.gid(PO), gm.gid(INVOICE)]
    n = len(gm.ctl.actions(gm.run))
    assert gm.ids(labelIds="INBOX") == expected
    assert gm.ids(q="in:sent") == []
    folders = [a["input"].get("folder") for a in gm.ctl.actions(gm.run)[n:]
               if a["operation"] == "read" and "page_size" in a["input"]]  # fmt: skip
    assert folders == ["inbox", "sent"]
    assert gm.ids(labelIds=["INBOX", "TRASH"]) == []  # two folders: no push-down, no match


def test_parser_bounds_unit():
    with pytest.raises(InvalidRequest):
        parse_query("{" * 40 + "a" + "}" * 40)
    with pytest.raises(InvalidRequest):
        parse_fields("x" * 1025)
    assert parse_query("after:1700000000") is not None
