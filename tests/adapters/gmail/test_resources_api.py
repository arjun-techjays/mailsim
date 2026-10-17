"""threads.*, labels.*, drafts.*, history.list and media uploads."""

from __future__ import annotations

import base64
import json

import pytest

from tests.adapters.gmail.conftest import INVOICE, PO, b64, raw_message

pytestmark = pytest.mark.db


def test_threads(gm):
    listed = gm.get("/threads")
    assert listed["resultSizeEstimate"] == 1
    (thread,) = listed["threads"]
    assert thread["snippet"].startswith("Purchase order") and thread["historyId"]
    full = gm.get(f"/threads/{thread['id']}", params={"format": "minimal"})
    assert [m["id"] for m in full["messages"]] == [gm.gid(INVOICE), gm.gid(PO)]
    assert "payload" not in full["messages"][0]
    gm.send("vendor@example.test", "other thread")
    page = gm.get("/threads", params={"maxResults": 1})
    assert len(page["threads"]) == 1 and page["nextPageToken"]
    rest = gm.get("/threads", params={"maxResults": 1, "pageToken": page["nextPageToken"]})
    assert rest["threads"][0]["id"] == thread["id"] and "nextPageToken" not in rest
    assert gm.get("/threads", params={"q": "nomatch"}) == {"resultSizeEstimate": 0}

    mod = gm.post(f"/threads/{thread['id']}/modify", json={"addLabelIds": ["STARRED"]})
    assert all("STARRED" in m["labelIds"] for m in mod["messages"])
    trashed = gm.post(f"/threads/{thread['id']}/trash")
    assert all("TRASH" in m["labelIds"] for m in trashed["messages"])
    restored = gm.post(f"/threads/{thread['id']}/untrash")
    assert all("INBOX" in m["labelIds"] for m in restored["messages"])
    gm.post(f"/threads/{thread['id']}/modify")
    gm.call("DELETE", f"/threads/{thread['id']}", 204)
    gm.call("GET", f"/threads/{thread['id']}", 404)


def test_labels_crud(gm):
    labels = gm.get("/labels")["labels"]
    assert [x["id"] for x in labels] == ["INBOX", "SENT", "DRAFT", "TRASH", "UNREAD", "STARRED",
                                         "ANSWERED", "DELETED"]  # fmt: skip
    created = gm.post("/labels", json={"name": "Vendors", "labelListVisibility": "labelHide"})
    assert created["type"] == "user" and created["labelListVisibility"] == "labelHide"
    lid = created["id"]
    assert gm.get(f"/labels/{lid}")["messagesTotal"] == 0
    for bad, code in (({"name": "vendors"}, 409), ({"name": "INBOX"}, 400), ({}, 400),
                      ({"name": "x" * 300}, 400), ({"name": "  "}, 400)):  # fmt: skip
        r = gm.call("POST", "/labels", code, json=bad)
        assert gm.error(r)[0] == code

    inv = gm.gid(INVOICE)
    gm.post(f"/messages/{inv}/modify", json={"addLabelIds": [lid]})
    got = gm.get(f"/labels/{lid}")
    assert (
        got["messagesTotal"],
        got["messagesUnread"],
        got["threadsTotal"],
        got["threadsUnread"],
    ) == (1, 1, 1, 1)
    inbox = gm.get("/labels/INBOX")
    assert inbox["type"] == "system" and inbox["messagesTotal"] == 2
    gm.call("GET", "/labels/SPAM", 404)
    gm.call("GET", f"/labels/Label_{b'missing'.hex()}", 404)

    # patch keeps settings, rename rewrites the label on messages and changes the id
    patched = gm.call(
        "PATCH",
        f"/labels/{lid}",
        json={"color": {"textColor": "#000000", "backgroundColor": "#ffffff"}},
    ).json()
    assert patched["labelListVisibility"] == "labelHide" and patched["color"]
    renamed = gm.call("PATCH", f"/labels/{lid}", json={"name": "Suppliers"}).json()
    assert renamed["id"] != lid and renamed["name"] == "Suppliers"
    assert renamed["id"] in gm.get(f"/messages/{inv}")["labelIds"]
    gm.call("GET", f"/labels/{lid}", 404)
    other = gm.post("/labels", json={"name": "Other"})["id"]
    r = gm.call("PUT", f"/labels/{other}", 409, json={"name": "suppliers"})
    assert gm.error(r)[1] == "CONFLICT"
    put = gm.call("PUT", f"/labels/{other}", json={"name": "Other"}).json()
    assert put["labelListVisibility"] == "labelShow"  # update replaces settings
    r = gm.call("PUT", f"/labels/{other}", 400, json={})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("PATCH", "/labels/INBOX", 400, json={"name": "x"})
    assert gm.error(r)[1] == "INVALID_REQUEST"

    gm.call("DELETE", f"/labels/{renamed['id']}", 204)
    assert gm.get(f"/messages/{inv}")["labelIds"] == ["INBOX", "UNREAD"]
    gm.call("DELETE", f"/labels/{other}", 204)
    gm.call("DELETE", f"/labels/{other}", 404)
    names = [x["name"] for x in gm.get("/labels")["labels"] if x["type"] == "user"]
    assert names == []


def test_labels_on_messages_are_listed_and_filterable(gm):
    gm.mb.modify_labels(
        gm.mb.resolve_external_id("gmail", "message", gm.gid(PO)), add=["Legacy Tag"]
    )
    user = [x for x in gm.get("/labels")["labels"] if x["type"] == "user"]
    assert [x["name"] for x in user] == ["Legacy Tag"]
    assert gm.ids(labelIds=user[0]["id"]) == [gm.gid(PO)]
    assert gm.ids(q="label:legacy-tag") == [gm.gid(PO)]
    assert gm.ids(q="has:userlabels") == [gm.gid(PO)]


def test_drafts_lifecycle(gm):
    raw = raw_message({"To": "vendor@example.test", "Subject": "Draft one"}, "first")
    created = gm.post("/drafts", json={"message": {"raw": b64(raw)}})
    did, first_msg = created["id"], created["message"]["id"]
    assert created["message"]["labelIds"] == ["DRAFT"]
    listed = gm.get("/drafts")
    assert listed["drafts"] == [
        {"id": did, "message": {"id": first_msg, "threadId": created["message"]["threadId"]}}
    ]
    got = gm.get(f"/drafts/{did}")
    assert got["id"] == did and got["message"]["payload"]["mimeType"] == "text/plain"
    assert gm.get(f"/drafts/{did}", params={"format": "minimal"})["message"]["id"] == first_msg

    upd = gm.call("PUT", f"/drafts/{did}", json={"message": {"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "Draft two"}, "second"))}}).json()  # fmt: skip
    assert upd["id"] == did and upd["message"]["id"] != first_msg  # stable draft id
    assert gm.get(f"/drafts/{did}", params={"format": "metadata"})["message"]["snippet"] == "second"

    sent = gm.post("/drafts/send", json={"id": did})
    assert sent["labelIds"] == ["SENT"]
    gm.call("GET", f"/drafts/{did}", 404)
    theirs = [m for m in gm.ev.state(gm.run)["messages"] if m["mailbox_id"] == gm.vendor_mb]
    assert [m["subject"] for m in theirs] == ["Draft two"]

    # send with replacement content; delete; listing filters and paging
    d2 = gm.post("/drafts", json={"message": {"raw": b64(raw)}})["id"]
    out = gm.post("/drafts/send", json={"id": d2, "message": {"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "Replaced"}))}})  # fmt: skip
    assert gm.get(f"/messages/{out['id']}", params={"format": "metadata",
                                                     "metadataHeaders": "Subject"})["payload"]["headers"][0]["value"] == "Replaced"  # fmt: skip
    d3 = gm.post("/drafts", json={"message": {"raw": b64(raw)}})["id"]
    d4 = gm.post(
        "/drafts", json={"message": {"raw": b64(raw_message({"Subject": "No recipients"}))}}
    )["id"]
    page = gm.get("/drafts", params={"maxResults": 1})
    assert len(page["drafts"]) == 1 and page["nextPageToken"]
    assert gm.get("/drafts", params={"q": "subject:recipients"})["drafts"][0]["id"] == d4
    gm.call("DELETE", f"/drafts/{d3}", 204)
    assert {d["id"] for d in gm.get("/drafts")["drafts"]} == {d4}
    assert gm.get("/drafts", params={"q": "zzz"}) == {"resultSizeEstimate": 0}

    for body in ({}, {"message": "x"}, {"message": {}}):
        r = gm.call("POST", "/drafts", 400, json=body)
        assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("POST", "/drafts/send", 400, json={})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("POST", "/drafts", 501, json={"message": {"raw": b64(raw_message(
        {"To": "vendor@example.test"}, attachment=b"%PDF"))}})  # fmt: skip
    assert gm.error(r)[1] == "UNSUPPORTED_OPERATION"


def test_draft_reply_keeps_thread(gm):
    inv = gm.get(f"/messages/{gm.gid(INVOICE)}", params={"format": "minimal"})
    raw = raw_message({"To": "vendor@example.test", "Subject": "Re: Invoice INV-001",
                       "In-Reply-To": "<invoice-001@example.test>"}, "draft reply")  # fmt: skip
    d = gm.post("/drafts", json={"message": {"raw": b64(raw), "threadId": inv["threadId"]}})
    assert d["message"]["threadId"] == inv["threadId"]
    sent = gm.post("/drafts/send", json={"id": d["id"]})
    assert sent["threadId"] == inv["threadId"]


def test_history(gm):
    start = gm.get("/profile")["historyId"]
    inv = gm.gid(INVOICE)
    gm.post(f"/messages/{inv}/modify", json={"removeLabelIds": ["UNREAD"]})
    sent = gm.send("vendor@example.test", "hist")
    gm.call("DELETE", f"/messages/{gm.gid(PO)}", 204)
    h = gm.get("/history", params={"startHistoryId": start})
    kinds = [next(k for k in ("messagesAdded", "messagesDeleted") if k in r) if
             ("messagesAdded" in r or "messagesDeleted" in r) else "label" for r in h["history"]]  # fmt: skip
    assert kinds == ["label", "messagesAdded", "messagesDeleted"]
    assert h["history"][0]["messages"][0]["id"] == inv
    assert h["history"][1]["messagesAdded"][0]["message"]["id"] == sent["id"]
    assert int(h["historyId"]) >= int(h["history"][-1]["id"])
    only_added = gm.get(
        "/history", params={"startHistoryId": start, "historyTypes": "messageAdded"}
    )
    assert [r["id"] for r in only_added["history"]] == [h["history"][1]["id"]]
    labelled = gm.get("/history", params={"startHistoryId": start, "labelId": "SENT"})
    assert [r["id"] for r in labelled["history"]] == [h["history"][1]["id"]]
    first = gm.get("/history", params={"startHistoryId": start, "maxResults": 1})
    assert len(first["history"]) == 1 and first["nextPageToken"]
    nxt = gm.get("/history", params={"startHistoryId": start, "pageToken": first["nextPageToken"]})
    assert [r["id"] for r in nxt["history"]] == [r["id"] for r in h["history"][1:]]
    latest = gm.get("/history", params={"startHistoryId": h["historyId"]})
    assert latest == {"historyId": h["historyId"]}
    for params in ({}, {"startHistoryId": "-1"}, {"startHistoryId": "1", "historyTypes": "bogus"},
                   {"startHistoryId": "1", "pageToken": "x"}):  # fmt: skip
        r = gm.call("GET", "/history", 400, params=params)
        assert gm.error(r)[1] == "INVALID_REQUEST", params
    from mailsim.engine.pagination import encode_cursor

    r = gm.call("GET", "/history", 400, params={"startHistoryId": "1",
                                                 "pageToken": encode_cursor("abc", "h")})  # fmt: skip
    assert gm.error(r)[1] == "INVALID_REQUEST"
    gm.ctl.reset_run(gm.run)
    gm.ctl.start_run(gm.run)
    r = gm.call("GET", "/history", 404, params={"startHistoryId": start})
    assert gm.error(r)[1] == "NOT_FOUND"  # stale after a reset: full sync required


def _multipart(meta: dict, raw: bytes, b64_part: bool = False) -> tuple[bytes, str]:
    boundary = "===bnd==="
    encoding = "base64" if b64_part else "binary"
    content = base64.b64encode(raw) if b64_part else raw
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: message/rfc822\r\n"
            f"Content-Transfer-Encoding: {encoding}\r\n\r\n").encode() + content + (
        f"\r\n--{boundary}--").encode()  # fmt: skip
    return body, f'multipart/related; boundary="{boundary}"'


def test_media_uploads(gm):
    up = "/upload/gmail/v1/users/me"
    raw = raw_message({"To": "vendor@example.test", "Subject": "uploaded"}, "media")
    sent = gm.call("POST", "/messages/send", base=up, params={"uploadType": "media"}, content=raw,
                   headers={"Content-Type": "message/rfc822"}).json()  # fmt: skip
    assert sent["labelIds"] == ["SENT"]
    body, ctype = _multipart({"labelIds": ["INBOX"]}, raw_message({"From": "vendor@example.test",
                             "To": "finance@example.test", "Subject": "imported"}), b64_part=True)  # fmt: skip
    ins = gm.call("POST", "/messages/import", base=up, params={"uploadType": "multipart"},
                  content=body, headers={"Content-Type": ctype}).json()  # fmt: skip
    assert ins["labelIds"] == ["INBOX"]
    body, ctype = _multipart({"labelIds": ["TRASH"]}, raw_message({"From": "vendor@example.test",
                             "Subject": "x"}))  # fmt: skip
    assert gm.call("POST", "/messages", base=up, content=body,
                   headers={"Content-Type": ctype}).json()["labelIds"] == ["TRASH"]  # fmt: skip
    body, ctype = _multipart({"message": {}}, raw)
    d = gm.call("POST", "/drafts", base=up, params={"uploadType": "multipart"}, content=body,
                headers={"Content-Type": ctype}).json()  # fmt: skip
    body, ctype = _multipart({"message": {}}, raw_message({"To": "vendor@example.test",
                             "Subject": "updated upload"}))  # fmt: skip
    upd = gm.call("PUT", f"/drafts/{d['id']}", base=up, params={"uploadType": "multipart"},
                  content=body, headers={"Content-Type": ctype}).json()  # fmt: skip
    assert upd["id"] == d["id"]
    body, ctype = _multipart({"id": d["id"]}, raw)
    assert gm.call("POST", "/drafts/send", base=up, params={"uploadType": "multipart"},
                   content=body, headers={"Content-Type": ctype}).json()["labelIds"] == ["SENT"]  # fmt: skip

    bad = [({"uploadType": "resumable"}, raw, "message/rfc822", 501),
           ({"uploadType": "weird"}, raw, "message/rfc822", 400),
           ({"uploadType": "media"}, b"  ", "message/rfc822", 400),
           ({"uploadType": "multipart"}, raw, "multipart/related", 400),
           ({"uploadType": "multipart"}, b"--b\r\n\r\n{}\r\n--b--", 'multipart/related; boundary="b"', 400),
           ({"uploadType": "multipart"}, b"--b\r\nX: y\r\n\r\nnot json\r\n--b\r\n\r\nraw\r\n--b--",
            'multipart/related; boundary="b"', 400),
           ({"uploadType": "multipart"}, b"--b\n\n[1]\n--b\n\nraw\n--b--", "multipart/related; boundary=b", 400),
           ({"uploadType": "multipart"}, b"--b\r\nno-separator--b\r\n\r\nraw\r\n--b--",
            'multipart/related; boundary="b"', 400),
           ({"uploadType": "multipart"},
            b"--b\r\n\r\n{}\r\n--b\r\nContent-Transfer-Encoding: base64\r\n\r\nabc\r\n--b--",
            'multipart/related; boundary="b"', 400)]  # fmt: skip
    for params, content, ctype, code in bad:
        r = gm.call("POST", "/messages/send", code, base=up, params=params, content=content,
                    headers={"Content-Type": ctype})  # fmt: skip
        assert gm.error(r)[0] == code, (params, content)
    body, ctype = _multipart({}, raw)
    lf = body.replace(b"\r\n", b"\n")
    assert gm.call("POST", "/messages/send", base=up, content=lf,
                   headers={"Content-Type": ctype}).json()["labelIds"] == ["SENT"]  # fmt: skip
