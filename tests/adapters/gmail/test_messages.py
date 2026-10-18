"""Gmail messages.* and users.getProfile over HTTP against real engine state."""

from __future__ import annotations

import base64

import pytest

from tests.adapters.gmail.conftest import INVOICE, PO, b64, raw_message

pytestmark = pytest.mark.db


def test_profile_and_user_id(gm):
    prof = gm.get("/profile")
    assert prof["emailAddress"] == "finance@example.test"
    assert prof["messagesTotal"] == 2 and prof["threadsTotal"] == 1
    assert int(prof["historyId"]) > 0
    assert gm.get("/profile", base="/gmail/v1/users/FINANCE@example.test")["messagesTotal"] == 2
    r = gm.call("GET", "/profile", 403, base="/gmail/v1/users/vendor@example.test")
    assert gm.error(r) == (403, "PERMISSION_DENIED", "forbidden")


def test_list_order_paging_and_estimate(gm):
    page = gm.get("/messages")
    assert [m["id"] for m in page["messages"]] == [gm.gid(PO), gm.gid(INVOICE)]  # newest first
    assert page["resultSizeEstimate"] == 2 and "nextPageToken" not in page
    first = gm.get("/messages", params={"maxResults": 1})
    assert len(first["messages"]) == 1 and first["nextPageToken"]
    second = gm.get("/messages", params={"maxResults": 1, "pageToken": first["nextPageToken"]})
    assert second["messages"][0]["id"] == gm.gid(INVOICE) and "nextPageToken" not in second
    assert gm.get("/messages", params={"maxResults": 9999})["resultSizeEstimate"] == 2
    empty = gm.get("/messages", params={"q": "nothing-matches-this"})
    assert empty == {"resultSizeEstimate": 0}
    r = gm.call("GET", "/messages", 400, params={"maxResults": 0})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("GET", "/messages", 400, params={"pageToken": "%%%"})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("GET", "/messages", 400, params={"maxResults": "x"})
    assert gm.error(r) == (400, "INVALID_REQUEST", "invalidArgument")


def test_trash_is_hidden_unless_asked(gm):
    gm.post(f"/messages/{gm.gid(PO)}/trash")
    assert gm.ids() == [gm.gid(INVOICE)]
    assert set(gm.ids(includeSpamTrash="true")) == {gm.gid(PO), gm.gid(INVOICE)}
    assert gm.ids(labelIds="TRASH") == [gm.gid(PO)]
    assert gm.ids(q="in:trash") == [gm.gid(PO)]
    assert len(gm.ids(q="in:anywhere")) == 2
    assert gm.ids(labelIds="SPAM") == []  # unmodelled system label: matches nothing
    r = gm.call("GET", "/messages", 404, params={"labelIds": "NOPE"})
    assert gm.error(r)[1] == "NOT_FOUND"


def test_get_formats(gm):
    gid = gm.gid(INVOICE)
    full = gm.get(f"/messages/{gid}")
    assert full["id"] == gid and full["labelIds"] == ["INBOX", "UNREAD"]
    assert full["snippet"].startswith("Please confirm the payment date")
    assert full["internalDate"].isdigit() and full["sizeEstimate"] > 0
    pl = full["payload"]
    assert pl["mimeType"] == "multipart/mixed" and pl["partId"] == ""
    text, pdf = pl["parts"]
    assert text["partId"] == "0" and base64.urlsafe_b64decode(text["body"]["data"]).startswith(
        b"Please confirm"
    )
    assert pdf["filename"] == "INV-001.pdf" and "data" not in pdf["body"]
    att = gm.get(f"/messages/{gid}/attachments/{pdf['body']['attachmentId']}")
    assert (
        base64.urlsafe_b64decode(att["data"]).startswith(b"%PDF")
        and att["size"] == pdf["body"]["size"]
    )
    r = gm.call("GET", f"/messages/{gid}/attachments/ffff", 404)
    assert gm.error(r)[1] == "NOT_FOUND"

    minimal = gm.get(f"/messages/{gid}", params={"format": "minimal"})
    assert "payload" not in minimal and "raw" not in minimal and minimal["historyId"]
    raw = gm.get(f"/messages/{gid}", params={"format": "raw"})
    assert base64.urlsafe_b64decode(raw["raw"]).startswith(
        b"Message-ID: <invoice-001@example.test>"
    )
    meta = gm.get(f"/messages/{gid}", params={"format": "metadata",
                                               "metadataHeaders": ["subject", "From"]})  # fmt: skip
    assert {h["name"] for h in meta["payload"]["headers"]} == {"Subject", "From"}
    all_headers = gm.get(f"/messages/{gid}", params={"format": "metadata"})["payload"]["headers"]
    assert len(all_headers) > 2
    r = gm.call("GET", f"/messages/{gid}", 400, params={"format": "html"})
    assert gm.error(r)[1] == "INVALID_REQUEST"


def test_fields_partial_response(gm):
    gid = gm.gid(INVOICE)
    out = gm.get(f"/messages/{gid}", params={"fields": "id,payload/parts(filename,partId)"})
    assert out == {"id": gid, "payload": {"parts": [{"filename": "", "partId": "0"},
                                                    {"filename": "INV-001.pdf", "partId": "1"}]}}  # fmt: skip
    listed = gm.get("/messages", params={"fields": "messages/id"})
    assert listed == {"messages": [{"id": gm.gid(PO)}, {"id": gid}]}
    r = gm.call("GET", f"/messages/{gid}", 400, params={"fields": "id,("})
    assert gm.error(r)[1] == "INVALID_REQUEST"


def test_unknown_and_foreign_ids_are_404(gm):
    for path in ("/messages/0123456789abcdef", "/messages/msg_internal", "/threads/zzz",
                 "/drafts/abc"):  # fmt: skip
        r = gm.call("GET", path, 404)
        assert gm.error(r) == (404, "NOT_FOUND", "notFound")
    # an id from another mailbox of the same run does not resolve here
    vendor = gm.ctl.inject(gm.run, gm.vendor_mb, {"from": "finance@example.test",
                           "to": ["vendor@example.test"], "subject": "theirs", "text": "x"})  # fmt: skip
    foreign = gm.ev.state(gm.run)["messages"]
    foreign_id = next(m["id"] for m in foreign if m["id"] == vendor["message_id"])
    from mailsim.providers.ids import gmail_id

    gm.call("GET", f"/messages/{gmail_id(foreign_id)}", 404)


def test_send_plain_and_to_self(gm):
    sent = gm.send("finance@example.test", "note to self", "body text")
    assert set(sent["labelIds"]) == {"SENT"}
    got = gm.get(f"/messages/{sent['id']}", params={"format": "metadata"})
    assert got["threadId"] == sent["threadId"]
    inbox = gm.get("/messages", params={"labelIds": ["INBOX", "UNREAD"], "q": "subject:note"})
    assert inbox["resultSizeEstimate"] == 1
    assert ("send", "ok") in gm.actions()


def test_send_reply_maps_to_engine_reply(gm):
    inv = gm.get(f"/messages/{gm.gid(INVOICE)}", params={"format": "metadata"})
    sent = gm.send("Example Vendor <vendor@example.test>", "Re: Invoice INV-001", "ack",
                   **{"In-Reply-To": "<invoice-001@example.test>",
                      "References": "<invoice-001@example.test>"})  # fmt: skip
    assert sent["threadId"] == inv["threadId"]
    ops = gm.actions()
    assert ("reply", "ok") in ops and ("send", "ok") not in ops
    # the original is now answered (sandbox label for the IMAP \\Answered flag)
    assert "ANSWERED" in gm.get(f"/messages/{gm.gid(INVOICE)}")["labelIds"]
    # the reply went to the vendor's (simulated) mailbox only
    theirs = [m for m in gm.ev.state(gm.run)["messages"] if m["mailbox_id"] == gm.vendor_mb]
    assert [m["subject"] for m in theirs] == ["Re: Invoice INV-001"]


def test_every_reply_is_one_engine_reply(gm):
    # PO is from procurement (outside the sandbox) -> use an internal thread instead
    gm.inject(From="vendor@example.test", To="finance@example.test", Cc="vendor@example.test",
              Subject="Group", **{"Message-ID": "<group-1@example.test>"})  # fmt: skip
    gm.send("vendor@example.test", "RE: group", "all", **{"In-Reply-To": "<group-1@example.test>"})
    # different subject and a Bcc: still ONE reply, composed fields kept, reply headers set
    out = gm.send("Vendor@Example.Test", "Another topic", "x",
                  **{"In-Reply-To": "<group-1@example.test>", "Bcc": "vendor@example.test"})  # fmt: skip
    ops = [a for a in gm.actions() if a[0] != "read"]
    assert ops.count(("reply", "ok")) == 2 and ("send", "ok") not in ops
    assert ("draft", "ok") not in ops
    got = gm.get(f"/messages/{out['id']}", params={"format": "metadata",
                                                    "metadataHeaders": ["In-Reply-To", "Subject"]})  # fmt: skip
    assert {h["name"]: h["value"] for h in got["payload"]["headers"]} == {
        "In-Reply-To": "<group-1@example.test>", "Subject": "Another topic"}  # fmt: skip


def test_reply_all_recipients(gm):
    gm.inject(From="vendor@example.test", To="finance@example.test, someone@example.test",
              Subject="Many", **{"Message-ID": "<many@example.test>"})  # fmt: skip
    # reply to everyone the engine's reply-all would address -> engine reply(reply_all=True)
    r = gm.call("POST", "/messages/send", 400, json={"raw": b64(raw_message(
        {"To": "vendor@example.test, someone@example.test", "Subject": "Re: Many",
         "In-Reply-To": "<many@example.test>"}))})  # fmt: skip
    assert gm.error(r)[1] == "EXTERNAL_DELIVERY_BLOCKED"  # someone@ is not simulated
    assert ("reply", "EXTERNAL_DELIVERY_BLOCKED") in gm.actions()


def test_reply_to_own_sent_message(gm):
    sent = gm.send("vendor@example.test", "Mine", "x")
    hdr = gm.get(f"/messages/{sent['id']}", params={"format": "metadata"})["payload"]["headers"]
    mid = next(h["value"] for h in hdr if h["name"] == "Message-ID")
    gm.send("vendor@example.test", "Re: Mine", "follow-up", References=mid)
    assert ("reply", "ok") in gm.actions()


def test_send_variants_and_errors(gm):
    html = raw_message({"To": "vendor@example.test", "Subject": "Re: Invoice INV-001",
                        "In-Reply-To": "<invoice-001@example.test>"}, "", html="<p>Hi <b>there</b></p>")  # fmt: skip
    # text part empty but present -> text used as-is; html-only message below
    gm.post("/messages/send", json={"raw": b64(html)})
    from email.message import EmailMessage

    em = EmailMessage()
    em["To"], em["Subject"] = "vendor@example.test", "html only"
    em.set_content("<p>Hello&amp;bye</p>", subtype="html")
    out = gm.post("/messages/send", json={"raw": b64(em.as_bytes()).rstrip("=")})
    assert gm.get(f"/messages/{out['id']}")["snippet"] == "Hello&bye"

    r = gm.call("POST", "/messages/send", 400, json={"raw": b64(raw_message(
        {"To": "x@real-domain.example", "Subject": "escape"}))})  # fmt: skip
    assert gm.error(r) == (400, "EXTERNAL_DELIVERY_BLOCKED", "failedPrecondition")
    r = gm.call("POST", "/messages/send", 501, json={"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "a"}, attachment=b"%PDF"))})  # fmt: skip
    assert gm.error(r) == (501, "UNSUPPORTED_OPERATION", "notImplemented")
    for body in ({}, {"raw": ""}, {"raw": "!!!!"}, {"raw": "a"}, {"raw": b64(b"   ")},
                 {"raw": b64(raw_message({"Subject": "no rcpt"}))},
                 {"raw": b64(raw_message({"To": "vendor@example.test"})), "threadId": 5}):  # fmt: skip
        r = gm.call("POST", "/messages/send", 400, json=body)
        assert gm.error(r)[1] == "INVALID_REQUEST", body
    r = gm.call("POST", "/messages/send", 404, json={
        "raw": b64(raw_message({"To": "vendor@example.test"})), "threadId": "0000000000000000"})  # fmt: skip
    assert gm.error(r)[1] == "NOT_FOUND"
    r = gm.call("POST", "/messages/send", 400, content=b"[1]",
                headers={"Content-Type": "application/json"})  # fmt: skip
    assert gm.error(r)[1] == "INVALID_REQUEST"


def test_insert_and_import(gm):
    raw = raw_message({"From": "vendor@example.test", "To": "finance@example.test",
                       "Subject": "inserted", "Date": "Thu, 15 Oct 2026 08:00:00 +0000"})  # fmt: skip
    label = gm.post("/labels", json={"name": "Ops"})["id"]
    out = gm.post(
        "/messages", json={"raw": b64(raw), "labelIds": ["INBOX", "UNREAD", "STARRED", label]}
    )
    assert set(out["labelIds"]) == {"INBOX", "UNREAD", "STARRED", label}
    assert [a[0] for a in gm.actions()[-4:]] == ["insert", "flag", "label", "read"]
    archived = gm.post("/messages/import", json={"raw": b64(raw)})
    assert archived["labelIds"] == []  # no labels: archived and read (Gmail insert semantics)
    trashed = gm.post("/messages", json={"raw": b64(raw), "labelIds": ["TRASH", "INBOX", "UNREAD"]})
    assert set(trashed["labelIds"]) == {"TRASH", "UNREAD"}
    # Sent holds only what this account sent: an inserted message is not sent mail (D-5)
    r = gm.call("POST", "/messages", 403, json={"raw": b64(raw), "labelIds": ["SENT"]})
    assert gm.error(r)[1] == "PERMISSION_DENIED"
    assert gm.actions()[-1] == ("insert", "PERMISSION_DENIED") and gm.ids(q="in:sent") == []
    # a user-label id spelling a system label is refused like DRAFT, and recorded (D-5)
    for name in ("SENT", "Draft", "inbox", "Archive"):
        fake = "Label_" + name.encode().hex()
        r = gm.call("POST", "/messages", 400, json={"raw": b64(raw), "labelIds": ["INBOX", fake]})
        assert gm.error(r)[1] == "INVALID_REQUEST"
        assert gm.actions()[-1] == ("insert", "INVALID_REQUEST")
    # no user label can carry a system name, so labels.create refuses them all, any case
    for name in ("Archive", "drafts", "Starred", "ANSWERED", "spam"):
        r = gm.call("POST", "/labels", 400, json={"name": name})
        assert gm.error(r)[1] == "INVALID_REQUEST", name
    r = gm.call("GET", "/labels/Label_" + b"Archive".hex(), 404)  # and an id spelling one is none
    theirs = [m for m in gm.ev.state(gm.run)["messages"] if m["mailbox_id"] == gm.vendor_mb]
    assert theirs == []  # insert never delivers
    r = gm.call("POST", "/messages", 400, json={"raw": b64(raw), "labelIds": ["DRAFT"]})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    r = gm.call("POST", "/messages", 501, json={"raw": b64(raw), "labelIds": ["SPAM"]})
    assert gm.error(r)[1] == "UNSUPPORTED_OPERATION"
    r = gm.call("POST", "/messages", 400, json={"raw": b64(raw_message({"To": "a@example.test"}))})
    assert gm.error(r)[1] == "INVALID_REQUEST"  # no From header
    r = gm.call("POST", "/messages", 400, json={"raw": b64(raw), "labelIds": "INBOX"})
    assert gm.error(r)[1] == "INVALID_REQUEST"


def test_modify_folders_flags_labels(gm):
    gid = gm.gid(INVOICE)
    lab = gm.post("/labels", json={"name": "Finance/Review"})["id"]

    def modify(add=(), remove=(), expect=200):
        r = gm.call("POST", f"/messages/{gid}/modify", expect,
                    json={"addLabelIds": list(add), "removeLabelIds": list(remove)})  # fmt: skip
        return r.json()["labelIds"] if expect == 200 else gm.error(r)

    assert set(modify(add=["STARRED", lab], remove=["UNREAD"])) == {"INBOX", "STARRED", lab}
    n = len(gm.actions())
    assert set(modify(add=["STARRED", lab], remove=["UNREAD"])) == {"INBOX", "STARRED", lab}
    assert [a for a in gm.actions()[n:] if a[0] != "read"] == []  # no-op costs no writes
    assert set(modify(add=["UNREAD", "ANSWERED", "DELETED"], remove=["STARRED", lab])) == {
        "INBOX", "UNREAD", "ANSWERED", "DELETED"}  # fmt: skip
    modify(remove=["ANSWERED", "DELETED"])
    assert modify(remove=["INBOX"]) == ["UNREAD"]  # archived
    assert modify(add=["INBOX"]) == ["INBOX", "UNREAD"]
    assert set(modify(add=["TRASH"])) == {"TRASH", "UNREAD"}
    assert set(modify(remove=["TRASH"])) == {"INBOX", "UNREAD"}  # restored to where it was
    # received mail never becomes "sent" by relabelling (QA B-5: forged evidence)
    assert modify(add=["SENT"], expect=403)[1] == "PERMISSION_DENIED"
    assert modify(add=["INBOX"]) == ["INBOX", "UNREAD"]  # already there
    assert modify(add=["INBOX", "TRASH"], expect=400)[1] == "INVALID_REQUEST"
    assert modify(add=["STARRED"], remove=["STARRED"], expect=400)[1] == "INVALID_REQUEST"
    assert modify(add=["DRAFT"], expect=400)[1] == "INVALID_REQUEST"
    for fake in ("Label_" + n.encode().hex() for n in ("SENT", "draft", "Inbox")):  # D-5
        assert modify(add=[fake], expect=400)[1] == "INVALID_REQUEST"
        assert modify(remove=[fake], expect=400)[1] == "INVALID_REQUEST"
        assert gm.actions()[-1] == ("label", "INVALID_REQUEST")  # recorded like the engine's
    assert modify(add=["IMPORTANT"], expect=501)[1] == "UNSUPPORTED_OPERATION"
    assert modify(add=["CATEGORY_UPDATES"], expect=501)[1] == "UNSUPPORTED_OPERATION"
    assert modify(add=["no-such-folder"], expect=404)[1] == "NOT_FOUND"
    assert modify(add=["Label_zz"], expect=404)[1] == "NOT_FOUND"
    assert modify(add=["Label_"], expect=404)[1] == "NOT_FOUND"
    r = gm.call("POST", f"/messages/{gid}/modify", 400, json={"addLabelIds": [1]})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    for body in ({"addLabelIds": ["STARRED"] * 51}, {"removeLabelIds": ["Label_" + "a" * 507]}):
        r = gm.call("POST", f"/messages/{gid}/modify", 400, json=body)
        assert gm.error(r)[1] == "INVALID_REQUEST"
    assert gm.post(f"/messages/{gid}/modify")["labelIds"] == ["INBOX", "UNREAD"]  # empty body


def test_trash_untrash_delete_batch(gm):
    inv, po = gm.gid(INVOICE), gm.gid(PO)
    assert gm.post(f"/messages/{inv}/trash")["labelIds"] == ["TRASH", "UNREAD"]
    assert gm.post(f"/messages/{inv}/untrash")["labelIds"] == ["INBOX", "UNREAD"]
    assert gm.call("POST", "/messages/batchModify", 204,
                   json={"ids": [inv, po], "addLabelIds": ["STARRED"]}).content == b""  # fmt: skip
    assert all("STARRED" in gm.get(f"/messages/{i}")["labelIds"] for i in (inv, po))
    r = gm.call("POST", "/messages/batchModify", 400, json={"ids": ["x"] * 1001})
    assert gm.error(r)[1] == "INVALID_REQUEST"
    gm.call("POST", "/messages/batchModify", 204)
    gm.call("DELETE", f"/messages/{inv}", 204)
    gm.call("GET", f"/messages/{inv}", 404)
    gm.call("POST", "/messages/batchDelete", 204, json={"ids": [po]})
    assert gm.get("/messages") == {"resultSizeEstimate": 0}
    gm.call("POST", "/messages/batchDelete", 204)
    assert ("delete", "ok") in gm.actions()


# ------------------------------------------------------------- QA-A1 regressions (B-1/B-3)
def test_draft_reply_send_and_history_are_clean_and_one_reply(gm):
    """B-1: drafts create/update/send + history never record a NOT_FOUND (history looks up
    replaced drafts internally). B-3: a draft whose In-Reply-To names a mailbox message is
    sent as ONE `reply` (ANSWERED, send_reply trigger), a plain draft as `send`."""
    h0 = gm.get("/profile")["historyId"]
    hdr = "<invoice-001@example.test>"
    raw = raw_message({"To": "vendor@example.test", "Subject": "Re: Invoice INV-001",
                       "In-Reply-To": hdr, "References": hdr}, "We will verify.")  # fmt: skip
    d = gm.post("/drafts", json={"message": {"raw": b64(raw)}})
    gm.call("PUT", f"/drafts/{d['id']}", json={"message": {"raw": b64(raw)}})
    sent = gm.post("/drafts/send", json={"id": d["id"]})
    assert "SENT" in sent["labelIds"]
    hist = gm.get("/history", params={"startHistoryId": h0})
    assert hist["history"]
    plain = gm.post("/drafts", json={"message": {"raw": b64(raw_message(  # unknown parent
        {"To": "vendor@example.test", "Subject": "fyi", "In-Reply-To": "<nope@x.test>"},
        "x"))}})  # fmt: skip
    gm.post("/drafts/send", json={"id": plain["id"]})
    gm.get("/history", params={"startHistoryId": h0})
    # drafts.update decides the threading from the NEW message: a reply draft rewritten
    # without reply headers is a plain send
    gone = gm.post("/drafts", json={"message": {"raw": b64(raw)}})
    gm.call("PUT", f"/drafts/{gone['id']}", json={"message": {"raw": b64(raw_message(
        {"To": "vendor@example.test", "Subject": "no longer a reply"}, "x"))}})  # fmt: skip
    gm.post("/drafts/send", json={"id": gone["id"]})
    acts = gm.actions()
    assert {o for _, o in acts} == {"ok"}, acts
    writes = [op for op, _ in acts if op not in ("read", "search", "create_run", "start")]
    assert writes == ["draft", "draft", "reply", "draft", "send", "draft", "draft", "send"]
    assert "ANSWERED" in gm.get(f"/messages/{gm.gid(INVOICE)}")["labelIds"]
    armed = [e["payload"]["id"] for e in gm.ev.events(gm.run) if e["type"] == "event.armed"]
    assert armed == ["vendor-bank-change"]
