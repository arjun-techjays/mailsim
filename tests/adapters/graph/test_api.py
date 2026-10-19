"""Graph v1.0 profile over raw HTTP (httpx via the FastAPI TestClient): every route, the
OData subset, Graph ids, error shapes and the security boundary."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from mailsim.adapters.graph import api as graph_api
from mailsim.adapters.graph import errors as graph_errors
from mailsim.adapters.graph import resources as res
from mailsim.domain.errors import RateLimited, UpstreamUnavailable
from mailsim.domain.models import CanonicalMessage
from tests.adapters.graph.conftest import BASE, open_agent
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
ANSWERED = "singleValueExtendedProperties($filter=id eq 'Integer 0x1081')"


def err(r, status, code):
    assert r.status_code == status, (r.status_code, r.text)
    body = r.json()["error"]
    assert body["code"] == code
    assert {"request-id", "client-request-id", "date"} <= set(body["innerError"])
    return body


def invoice(w):
    return w.by_subject("Invoice INV-001")


# ------------------------------------------------------------------------- auth & identity


def test_authentication_is_always_an_agent_key(world, monkeypatch):
    monkeypatch.setenv("MAILSIM_LOCAL_NO_AUTH", "1")  # never honoured by the Graph adapter
    anon = TestClient(world.app, raise_server_exceptions=False)
    r = anon.get(f"{BASE}/me/messages")
    err(r, 401, "InvalidAuthenticationToken")
    assert r.headers["WWW-Authenticate"].startswith("Bearer")
    err(anon.get(f"{BASE}/me/messages", headers={"Authorization": "Basic abc"}), 401,
        "InvalidAuthenticationToken")  # fmt: skip
    err(anon.get(f"{BASE}/me/messages", headers={"Authorization": "Bearer msk_bad"}), 401,
        "InvalidAuthenticationToken")  # fmt: skip
    err(anon.post(f"{BASE}/me/anything"), 401, "InvalidAuthenticationToken")
    for role in ("controller", "evaluator"):
        hdr = {"Authorization": f"Bearer {world.issue(role)}"}
        err(anon.get(f"{BASE}/me/messages", headers=hdr), 403, "ErrorAccessDenied")


def test_users_path_must_be_the_credentials_mailbox(world):
    me = world.get("/me")
    assert me["mail"] == "finance@example.test" and me["id"] == res.user_id(world.mailbox)
    for ident in ("finance@example.test", "FINANCE@example.test", world.mailbox, me["id"]):
        assert len(world.get(f"/users/{ident}/messages")["value"]) == 2
    err(world.call("GET", "/users/vendor@example.test/messages"), 403, "ErrorAccessDenied")
    err(world.call("GET", f"/users/{world.vendor}/mailFolders"), 403, "ErrorAccessDenied")
    vendor_key = world.issue(mailbox=world.vendor)  # a key bound to a non-agent mailbox
    r = world.call("GET", "/me/messages", headers={"Authorization": f"Bearer {vendor_key}"})
    err(r, 403, "ErrorAccessDenied")


def test_error_shape_and_request_ids(world):
    r = world.call("GET", "/me/messages/AAMknope", headers={"client-request-id": "abc-123",
                                                              "x-request-id": "rid-1"})  # fmt: skip
    body = err(r, 404, "ErrorItemNotFound")
    assert body["innerError"]["client-request-id"] == "abc-123" == r.headers["client-request-id"]
    assert body["innerError"]["request-id"] == "rid-1" == r.headers["request-id"]
    r = world.call("GET", "/me/messages/AAMknope", headers={"client-request-id": "bad id!"})
    assert r.json()["error"]["innerError"]["client-request-id"] != "bad id!"
    r = world.call("POST", "/me/messages", content=b"{not json",
                   headers={"Content-Type": "application/json"})  # fmt: skip
    err(r, 400, "ErrorInvalidRequest")


def test_unconfigured_server_and_unexpected_errors(world, monkeypatch):
    bare = FastAPI()
    bare.include_router(graph_api.router)
    client = TestClient(bare, raise_server_exceptions=False)
    r = client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {world.key}"})
    err(r, 501, "ErrorNotSupported")

    def boom(*a, **k):
        raise ValueError("kaboom")

    monkeypatch.setattr(graph_api, "all_messages", boom)
    body = err(world.call("GET", "/me/messages"), 500, "generalException")
    assert "kaboom" not in body["message"]


def test_error_mapping_for_engine_codes():
    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    r = graph_errors.error_response(req, RateLimited("slow down"))
    assert (r.status_code, r.headers["Retry-After"]) == (429, "1")
    r = graph_errors.error_response(req, UpstreamUnavailable("llm down"))
    assert r.status_code == 503 and b"ErrorServiceUnavailable" in r.body
    from starlette.exceptions import HTTPException

    for status, code in ((404, b"ErrorItemNotFound"), (405, b"ErrorNotSupported"),
                         (418, b"ErrorInvalidRequest")):  # fmt: skip
        r = graph_errors.error_response(req, HTTPException(status, "x"))
        assert r.status_code == status and code in r.body


def test_run_state_and_budget_errors(env, tmp_path, world):
    world.ctl.reset_run(world.run)  # not RUNNING until started again
    err(world.call("GET", "/me/messages"), 412, "ErrorPreconditionFailed")
    world.ctl.start_run(world.run)

    def tight(s):
        open_agent(s)
        s["version"] = 2
        s["evaluation"]["limits"]["max_agent_actions"] = 1

    run = world.ctl.create_run(env.version(write_variant(tmp_path, tight, "tight.yaml")))
    world.ctl.start_run(run)
    with env.session_factory.begin() as s:
        from mailsim.api.auth import issue_key

        key = issue_key(s, tenant_id=env.scope.tenant_id, project_id=env.scope.project_id,
                        role="agent", run_id=run,
                        mailbox_id=world.ctl.mailbox_id_for(run, "finance_manager")).api_key  # fmt: skip
    hdr = {"Authorization": f"Bearer {key}"}
    first = world.get("/me/messages", headers=hdr)["value"][0]["id"]
    world.patch(f"/me/messages/{first}", {"isRead": True}, headers=hdr)
    err(world.call("PATCH", f"/me/messages/{first}", json={"isRead": False}, headers=hdr), 429,
        "ErrorQuotaExceeded")  # fmt: skip


def test_unsupported_paths_fail_explicitly(world):
    mid = invoice(world)["id"]
    err(world.call("POST", f"/me/messages/{mid}/attachments", json={}), 501, "ErrorNotSupported")
    err(world.call("POST", "/me/mailFolders", json={"displayName": "x"}), 501, "ErrorNotSupported")
    err(world.call("PUT", f"/me/messages/{mid}", json={}), 501, "ErrorNotSupported")
    err(world.call("GET", "/me/events"), 501, "ErrorNotSupported")


# --------------------------------------------------------------------------- listing


def test_list_order_paging_select_count(world):
    for i in range(3):
        world.inject(f"page-{i}")
    everything = world.messages()
    assert len(everything) == 5
    stamps = [m["receivedDateTime"] for m in everything]
    assert stamps == sorted(stamps, reverse=True), "default order: receivedDateTime desc"
    asc = world.messages(**{"$orderby": "receivedDateTime asc"})
    assert [m["subject"] for m in asc][:2] == ["Invoice INV-001", "Re: Invoice INV-001"]
    assert [m["subject"] for m in asc][2:] == ["page-0", "page-1", "page-2"]  # ties: stable
    by_subject = world.messages(**{"$orderby": "subject desc,receivedDateTime"})
    assert [m["subject"] for m in by_subject][:2] == ["Re: Invoice INV-001", "page-2"]
    page = world.get("/me/messages", params={"$top": 2, "$count": "true", "$select": "subject"})
    assert page["@odata.count"] == 5 and len(page["value"]) == 2
    assert set(page["value"][0]) == {"@odata.etag", "id", "subject"}
    seen = [m["id"] for m in page["value"]]
    while "@odata.nextLink" in page:
        assert "%24select=subject" in page["@odata.nextLink"] or "$select=subject" in page[
            "@odata.nextLink"]  # fmt: skip
        page = world.client.get(page["@odata.nextLink"]).json()
        seen += [m["id"] for m in page["value"]]
    assert seen == [m["id"] for m in everything]
    token = graph_api.odata.encode_token({"o": 4})
    assert len(world.get("/me/messages", params={"$skiptoken": token})["value"]) == 1
    assert len(world.get("/me/messages", params={"$skip": 3})["value"]) == 2
    r = world.call("GET", "/me/messages", headers={"Prefer": "odata.maxpagesize=1"})
    assert len(r.json()["value"]) == 1 and r.headers["Preference-Applied"] == "odata.maxpagesize=1"
    err(world.call("GET", "/me/messages", params={"$count": "maybe"}), 400, "ErrorInvalidRequest")
    err(world.call("GET", "/me/messages", params={"$top": 0}), 400, "ErrorInvalidRequest")
    err(world.call("GET", "/me/messages", params={"$select": "nope"}), 400, "ErrorInvalidRequest")
    err(world.call("GET", "/me/messages", params={"$apply": "x"}), 400, "ErrorNotSupported")
    err(world.call("GET", "/me/messages", headers={"Prefer": "odata.maxpagesize=0"}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    assert world.call("GET", "/me/messages/$count", expect=200).text == "5"
    assert world.call("GET", "/me/mailFolders/inbox/messages/$count", expect=200,
                      params={"$filter": "startswith(subject,'page')"}).text == "3"  # fmt: skip
    err(world.call("GET", "/me/messages/$count", params={"$top": 1}), 400, "ErrorNotSupported")


def test_filter_and_search(world):
    world.inject("filter-me", text="needle in body")
    unread = world.messages(**{"$filter": "isRead eq false and hasAttachments eq false"})
    assert {m["subject"] for m in unread} == {"Re: Invoice INV-001", "filter-me"}
    when = world.messages(**{"$filter": "receivedDateTime ge 2026-10-15T08:40:00Z and "
                                        "receivedDateTime le 2026-10-15T08:50:00Z"})  # fmt: skip
    assert [m["subject"] for m in when] == ["Re: Invoice INV-001"]
    sender = world.messages(**{"$filter": "from/emailAddress/address eq 'VENDOR@example.test'"})
    assert {m["subject"] for m in sender} == {"Invoice INV-001", "filter-me"}
    conv = invoice(world)["conversationId"]
    thread = world.messages(**{"$filter": f"conversationId eq '{conv}'"})
    assert {m["subject"] for m in thread} == {"Invoice INV-001", "Re: Invoice INV-001"}
    either = world.messages(**{"$filter": "subject eq 'filter-me' or hasAttachments eq true"})
    assert len(either) == 2
    hits = world.messages(**{"$search": '"needle"'})
    assert [m["subject"] for m in hits] == ["filter-me"]
    assert world.messages("sentitems", **{"$search": "needle"}) == []
    err(world.call("GET", "/me/messages", params={"$search": "x", "$filter": "isRead eq true"}),
        400, "ErrorNotSupported")  # fmt: skip
    err(world.call("GET", "/me/messages", params={"$filter": "isRead eq"}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    err(world.call("GET", "/me/messages", params={"$filter": "bodyPreview eq 'x'"}), 400,
        "ErrorNotSupported")  # fmt: skip


def test_kql_search(world):
    world.inject("kql-one", text="urgent bank change")
    subjects = lambda **q: sorted(m["subject"] for m in world.messages(**q))  # noqa: E731
    assert subjects(**{"$search": '"subject:invoice"'}) == [
        "Invoice INV-001",
        "Re: Invoice INV-001",
    ]
    assert subjects(**{"$search": '"from:vendor AND hasAttachments:true"'}) == ["Invoice INV-001"]
    assert subjects(**{"$search": '"body:urgent OR subject:\\"INV-001\\""'}) == [
        "Invoice INV-001", "Re: Invoice INV-001", "kql-one"]  # fmt: skip
    assert subjects(**{"$search": '"subject:invoice NOT hasAttachments:true"'}) == [
        "Re: Invoice INV-001"]  # fmt: skip
    assert subjects(**{"$search": '"bank subject:kql"'}) == ["kql-one"]  # free text + property
    assert subjects(**{"$search": '"isRead:false subject:kql"'}) == ["kql-one"]
    assert subjects(**{"$search": '"received>=2100-01-01"'}) == []
    assert "kql-one" in subjects(**{"$search": '"received:2000-01-01..2100-01-01"'})
    assert world.messages("sentitems", **{"$search": '"subject:invoice"'}) == []
    # a phrase alone, two words (AND, any order), a phrase plus a word
    assert subjects(**{"$search": '"\\"bank change\\""'}) == ["kql-one"]
    assert subjects(**{"$search": '"\\"change bank\\""'}) == []
    assert subjects(**{"$search": '"change urgent"'}) == ["kql-one"]
    assert subjects(**{"$search": '"\\"bank change\\" urgent"'}) == ["kql-one"]
    assert subjects(**{"$search": '"\\"bank change\\" nothing"'}) == []
    assert subjects(**{"$search": '"re:meeting"'}) == []  # unknown prefix: plain text
    body = err(world.call("GET", "/me/messages", params={"$search": '"isRead:maybe"'}), 400,
               "ErrorInvalidRequest")  # fmt: skip
    assert "true or false" in body["message"]
    err(world.call("GET", "/me/messages", params={"$search": '"received:9999-12-31"'}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    err(world.call("GET", "/me/messages", params={"$search": '"(subject:x"'}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    count = world.call("GET", "/me/messages/$count", expect=200,
                       params={"$search": '"subject:invoice"'})  # fmt: skip
    assert count.text == "2"


def test_get_select_expand_prefer_and_mime(world):
    m = invoice(world)
    assert m["hasAttachments"] and m["internetMessageId"] == "<invoice-001@example.test>"
    assert m["parentFolderId"] == world.get("/me/mailFolders/inbox")["id"]
    one = world.get(f"/me/messages/{m['id']}",
                    params={"$select": "subject,internetMessageHeaders"})  # fmt: skip
    names = {h["name"] for h in one["internetMessageHeaders"]}
    assert {"Message-ID", "Subject", "From"} <= names and "body" not in one
    exp = world.get(f"/me/messages/{m['id']}", params={"$expand": f"attachments,{ANSWERED}"})
    assert exp["singleValueExtendedProperties"] == []
    assert exp["attachments"][0]["name"] == "INV-001.pdf"
    assert base64.b64decode(exp["attachments"][0]["contentBytes"]).startswith(b"%PDF")
    r = world.call("GET", f"/me/messages/{m['id']}", 200,
                   headers={"Prefer": 'outlook.body-content-type="html"'})  # fmt: skip
    assert r.json()["body"]["contentType"] == "html" and "<pre>" in r.json()["body"]["content"]
    assert r.headers["Preference-Applied"] == 'outlook.body-content-type="html"'
    mime = world.call("GET", f"/me/messages/{m['id']}/$value", 200)
    assert b"Subject: Invoice INV-001" in mime.content
    err(world.call("GET", f"/me/messages/{m['id']}", params={"$top": 1}), 400, "ErrorNotSupported")
    err(world.call("GET", "/me/messages/msg_internal_ids_are_not_graph_ids"), 404,
        "ErrorItemNotFound")  # fmt: skip


def test_attachments(world):
    m = invoice(world)
    atts = world.get(f"/me/messages/{m['id']}/attachments")["value"]
    (att,) = atts
    assert att["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert (att["contentType"], att["isInline"], att["contentId"]) == ("application/pdf", False,
                                                                       None)  # fmt: skip
    one = world.get(f"/me/messages/{m['id']}/attachments/{att['id']}")
    assert one["contentBytes"] == att["contentBytes"] and one["size"] == att["size"]
    raw = world.call("GET", f"/me/messages/{m['id']}/attachments/{att['id']}/$value", 200)
    assert raw.headers["content-type"] == "application/pdf" and raw.content.startswith(b"%PDF")
    # recorded bytes are a download, never rendered (shared api.downloads headers)
    assert raw.headers["content-disposition"] == 'attachment; filename="INV-001.pdf"'
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["content-security-policy"].startswith("sandbox")
    err(world.call("GET", f"/me/messages/{m['id']}/attachments/AAMknope"), 404,
        "ErrorItemNotFound")  # fmt: skip


def test_attachment_resource_without_bytes_has_no_content(world):
    m = world.mb().list_messages(folder="inbox").items
    msg = next(x for x in m if x.attachments)
    doc = res.attachment(msg.attachments[0], msg, None)
    assert "contentBytes" not in doc and doc["size"] > 0


def test_attachment_bytes_are_audited_facade_reads(world):
    m = invoice(world)

    def reads():
        return [a for a in world.ev.actions(world.run)
                if a["actor"] == "agent" and "attachment_id" in a["input"]]  # fmt: skip

    (att,) = world.get(f"/me/messages/{m['id']}/attachments")["value"]
    assert len(reads()) == 1  # the list's contentBytes
    world.get(f"/me/messages/{m['id']}/attachments/{att['id']}")
    world.call("GET", f"/me/messages/{m['id']}/attachments/{att['id']}/$value", 200)
    world.get(f"/me/messages/{m['id']}", params={"$expand": "attachments"})
    world.get("/me/messages", params={"$expand": "attachments", "$top": 100})
    assert len(reads()) == 5 and {a["operation"] for a in reads()} == {"read"}


# --------------------------------------------------------------------------- folders


def test_mail_folders(world):
    folders = world.get("/me/mailFolders")["value"]
    by_name = {f["displayName"]: f for f in folders}
    assert by_name["Inbox"]["totalItemCount"] == 2 and by_name["Inbox"]["childFolderCount"] == 0
    for wk, name in (("inbox", "Inbox"), ("SentItems", "Sent Items"), ("drafts", "Drafts"),
                     ("deleteditems", "Deleted Items"), ("archive", "Archive")):  # fmt: skip
        assert world.get(f"/me/mailFolders/{wk}")["id"] == by_name[name]["id"]
    sent_id = by_name["Sent Items"]["id"]
    assert world.get(f"/me/mailFolders/{sent_id}")["displayName"] == "Sent Items"
    assert world.get(f"/me/mailFolders/{sent_id}/childFolders")["value"] == []
    assert world.get(f"/me/mailFolders/{by_name['Inbox']['id']}/messages")["value"]
    err(world.call("GET", "/me/mailFolders/junkemail"), 404, "ErrorItemNotFound")
    err(world.call("GET", "/me/mailFolders/nope/childFolders"), 404, "ErrorItemNotFound")
    err(world.call("GET", "/me/mailFolders/nope/messages"), 404, "ErrorItemNotFound")


def test_folder_lookup_reports_missing_folders(world, monkeypatch):
    ctx = graph_api.Ctx(None, None, world.mb())  # type: ignore[arg-type]
    ref = graph_api.FolderRef("custom", "name:custom")
    with pytest.raises(graph_api.NotFound):
        graph_api.folder_record(ctx, ref)
    monkeypatch.setattr(graph_api, "_folders", lambda ctx: [])
    err(world.call("GET", "/me/mailFolders/inbox"), 404, "ErrorItemNotFound")


# ----------------------------------------------------------------------- mutations


def test_patch_flags_categories_and_replied_marker(world):
    mid = invoice(world)["id"]
    out = world.patch(f"/me/messages/{mid}", {"isRead": True, "categories": ["A", "B", "A"],
                                              "flag": {"flagStatus": "flagged"}})  # fmt: skip
    assert out["isRead"] and out["categories"] == ["A", "B"] and out["id"] == mid
    assert out["flag"]["flagStatus"] == "flagged"
    out = world.patch(f"/me/messages/{mid}", {"categories": ["B", "C"],
                                              "flag": {"flagStatus": "complete"}})  # fmt: skip
    assert out["categories"] == ["B", "C"] and out["flag"]["flagStatus"] == "notFlagged"
    prop = {"id": "Integer 0x1081", "value": "102"}
    world.patch(f"/me/messages/{mid}", {"singleValueExtendedProperties": [prop]})
    got = world.get(f"/me/messages/{mid}", params={"$expand": ANSWERED})
    assert got["singleValueExtendedProperties"] == [prop]
    assert "ANSWERED" in world.mb().get_message(world.mb().resolve_external_id(
        "graph", "message", mid)).flags  # fmt: skip
    world.patch(f"/me/messages/{mid}", {"singleValueExtendedProperties": [
        {"id": "integer 0x1081", "value": "0"}], "isRead": False})  # fmt: skip
    got = world.get(f"/me/messages/{mid}", params={"$expand": ANSWERED})
    assert got["singleValueExtendedProperties"] == [] and got["isRead"] is False
    unchanged = world.patch(f"/me/messages/{mid}", {"categories": ["B", "C"]})
    assert unchanged["categories"] == ["B", "C"]
    for bad in ({"isRead": "yes"}, {"flag": {"flagStatus": "maybe"}}, {"categories": "x"},
                {"categories": [1]}, {"subject": "not a draft"},
                {"singleValueExtendedProperties": "x"}):  # fmt: skip
        err(world.call("PATCH", f"/me/messages/{mid}", json=bad), 400, "ErrorInvalidRequest")
    for bad in ({"singleValueExtendedProperties": [{"id": "String 0x0037", "value": "x"}]},
                {"importance": "high"}, {"replyTo": [{"emailAddress": {"address": "a@b.c"}}]}):  # fmt: skip
        err(world.call("PATCH", f"/me/messages/{mid}", json=bad), 501, "ErrorNotSupported")
    assert world.patch(f"/me/messages/{mid}", None)["id"] == mid  # empty PATCH: no-op


def test_delete_move_copy(world):
    mid = invoice(world)["id"]
    archive = world.get("/me/mailFolders/archive")["id"]
    moved = world.post(f"/me/messages/{mid}/move", {"DestinationId": archive})
    assert moved["parentFolderId"] == archive and moved["id"] == mid
    copy = world.post(f"/me/messages/{mid}/copy", {"destinationId": "inbox"})
    assert copy["id"] != mid and copy["parentFolderId"] == world.get("/me/mailFolders/inbox")["id"]
    world.call("DELETE", f"/me/messages/{copy['id']}", 204)
    trashed = world.get(f"/me/messages/{copy['id']}")
    assert trashed["parentFolderId"] == world.get("/me/mailFolders/deleteditems")["id"]
    world.call("DELETE", f"/me/messages/{copy['id']}", 204)
    err(world.call("GET", f"/me/messages/{copy['id']}"), 404, "ErrorItemNotFound")
    err(world.call("POST", f"/me/messages/{mid}/move", json={}), 400, "ErrorInvalidRequest")
    err(world.call("POST", f"/me/messages/{mid}/move", json={"destinationId": "nope"}), 404,
        "ErrorItemNotFound")  # fmt: skip
    world.call("POST", f"/me/messages/{mid}/permanentDelete", 204)
    err(world.call("POST", f"/me/messages/{mid}/permanentDelete"), 404, "ErrorItemNotFound")


def test_send_mail_and_its_errors(world):
    msg = {"subject": "hello", "body": {"contentType": "HTML", "content": "<b>hi</b> &amp; bye"},
           "toRecipients": [{"emailAddress": {"address": "vendor@example.test"}}],
           "ccRecipients": [{"emailAddress": {"address": "procurement@example.test",
                                              "name": "P"}}],
           "bccRecipients": [], "from": {"emailAddress": {"address": "Finance@example.test"}}}  # fmt: skip
    err(
        world.call("POST", "/me/sendMail", json={"message": msg}),
        403,
        "ErrorMessageSubmissionBlocked",
    )  # procurement@ is not a simulated mailbox
    msg["ccRecipients"] = []
    assert world.post("/me/sendMail", {"Message": msg, "SaveToSentItems": False}, 202) is None
    (sent,) = world.messages("sentitems")
    assert sent["body"] == {"contentType": "html", "content": "<b>hi</b> &amp; bye"}
    assert sent["bodyPreview"] == "hi & bye" and sent["isRead"] and not sent["isDraft"]
    text = world.get(f"/me/messages/{sent['id']}",
                     headers={"Prefer": "outlook.body-content-type=text"})  # fmt: skip
    assert text["body"]["contentType"] == "text" and "hi" in text["body"]["content"]
    for bad in ({}, {"message": "x"}, {"message": msg, "saveToSentItems": "yes"},
                {"message": {**msg, "toRecipients": "x"}},
                {"message": {**msg, "toRecipients": [{"emailAddress": {}}]}},
                {"message": {**msg, "body": "x"}},
                {"message": {**msg, "body": {"contentType": "rtf", "content": "x"}}},
                {"message": {**msg, "subject": 5}}):  # fmt: skip
        err(world.call("POST", "/me/sendMail", json=bad), 400, "ErrorInvalidRequest")
    attach = {**msg, "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment",
                                      "name": "a.txt", "contentBytes": "eA=="}]}  # fmt: skip
    err(world.call("POST", "/me/sendMail", json={"message": attach}), 501, "ErrorNotSupported")
    spoof = {**msg, "from": {"emailAddress": {"address": "ceo@example.test"}}}
    err(world.call("POST", "/me/sendMail", json={"message": spoof}), 403, "ErrorAccessDenied")


def test_system_names_are_not_categories_and_isdraft_is_read_only(world):
    """D-5: a category named like a system label (Sent, Draft, Inbox, ...) would forge "sent"
    / "drafted" evidence; it is refused before anything is written. isDraft never changes."""
    mid = invoice(world)["id"]
    for name in ("SENT", "Draft", "inbox", "Unread"):
        r = world.call("PATCH", f"/me/messages/{mid}", json={"isRead": True, "categories": [name]})
        err(r, 400, "ErrorInvalidRequest")
    after = world.get(f"/me/messages/{mid}")
    assert after["isRead"] is False and after["categories"] == []  # nothing was applied
    acts = [(a["actor"], a["operation"], a["outcome"]) for a in world.ctl.actions(world.run)]
    assert acts.count(("agent", "label", "INVALID_REQUEST")) == 4  # each refusal recorded
    assert world.patch(f"/me/messages/{mid}", {"isDraft": True})["isDraft"] is False
    for cats in (["c"] * 51, ["x" * 226]):  # the shared caps: 50 names of <= 225 characters
        err(world.call("PATCH", f"/me/messages/{mid}", json={"categories": cats}), 400,
            "ErrorInvalidRequest")  # fmt: skip
    assert len(world.patch(f"/me/messages/{mid}", {"categories": ["x" * 225]})["categories"]) == 1
    assert world.messages("sentitems") == [] and world.messages("drafts") == []


def test_stored_system_names_never_break_a_category_replacement(world):
    """Seeded mail may carry a stored label named like a system label: replacing the
    categories leaves it alone instead of failing halfway."""
    mid = invoice(world)["id"]
    inv = next(m for m in world.mb().list_messages(folder="inbox").items
               if m.subject == "Invoice INV-001")  # fmt: skip
    world.env.engine.modify_labels(world.env.scope, world.run, world.mailbox, inv.id,
                                   add=["SENT"], actor="controller")  # fmt: skip
    upd = world.patch(f"/me/messages/{mid}", {"isRead": True, "categories": ["Review"]})
    assert upd["isRead"] is True and "Review" in upd["categories"]
    assert world.patch(f"/me/messages/{mid}", {"categories": []})["isRead"] is True


def test_drafts_lifecycle(world):
    draft = world.post("/me/messages", {"subject": "d1", "toRecipients": [
        {"emailAddress": {"address": "vendor@example.test"}}]})  # fmt: skip
    assert draft["isDraft"] and draft["sentDateTime"] is None
    drafts_id = world.get("/me/mailFolders/drafts")["id"]
    assert draft["parentFolderId"] == drafts_id
    upd = world.patch(f"/me/messages/{draft['id']}", {
        "subject": "d2", "body": {"contentType": "text", "content": "body"},
        "ccRecipients": [{"emailAddress": {"address": "finance@example.test"}}],
        "categories": ["Pending"], "isRead": True})  # fmt: skip
    assert upd["id"] == draft["id"] and upd["subject"] == "d2" and upd["categories"] == ["Pending"]
    assert [r["emailAddress"]["address"] for r in upd["ccRecipients"]] == ["finance@example.test"]
    assert world.get(f"/me/messages/{draft['id']}")["subject"] == "d2"
    world.post(f"/me/messages/{draft['id']}/send", None, 202)
    err(world.call("GET", f"/me/messages/{draft['id']}"), 404, "ErrorItemNotFound")
    assert [m["subject"] for m in world.messages("sentitems")] == ["d2"]
    assert world.messages("drafts") == []
    mid = invoice(world)["id"]
    err(world.call("POST", f"/me/messages/{mid}/send"), 409, "ErrorIrresolvableConflict")
    empty = world.post("/me/messages", None)
    assert empty["isDraft"] and empty["toRecipients"] == []
    err(world.call("POST", f"/me/messages/{empty['id']}/send"), 400, "ErrorInvalidRequest")


def test_reply_family(world):
    inv = invoice(world)
    mid = inv["id"]
    r = world.post(f"/me/messages/{mid}/createReply", {"Comment": "c1",
                   "Message": {"ccRecipients": [{"emailAddress": {"address": "finance@example.test"}}]}})  # fmt: skip
    assert r["subject"] == "RE: Invoice INV-001" and r["isDraft"]
    assert r["conversationId"] == inv["conversationId"] and r["body"]["content"] == "c1"
    assert [x["emailAddress"]["address"] for x in r["ccRecipients"]] == ["finance@example.test"]
    world.inject("group", cc=["finance@example.test"], to=["finance@example.test",
                                                          "vendor@example.test"])  # fmt: skip
    group = world.by_subject("group")
    ra = world.post(f"/me/messages/{group['id']}/createReplyAll", {})
    assert [x["emailAddress"]["address"] for x in ra["toRecipients"]] == ["vendor@example.test"]
    assert ra["ccRecipients"] == []
    fw = world.post(f"/me/messages/{mid}/createForward", {"comment": "see below",
                    "toRecipients": [{"emailAddress": {"address": "vendor@example.test"}}]})  # fmt: skip
    assert fw["subject"] == "FW: Invoice INV-001" and "Forwarded message" in fw["body"]["content"]
    world.post(f"/me/messages/{mid}/reply", {"comment": "direct"}, 202)
    world.post(f"/me/messages/{group['id']}/replyAll", {"comment": "all"}, 202)
    world.post(f"/me/messages/{mid}/forward", {"comment": "fwd", "toRecipients": [
        {"emailAddress": {"address": "vendor@example.test"}}]}, 202)  # fmt: skip
    world.post(f"/me/messages/{mid}/forward", {"comment": "fwd2", "message": {
        "toRecipients": [{"emailAddress": {"address": "vendor@example.test"}}]}}, 202)  # fmt: skip
    world.post(f"/me/messages/{mid}/reply", {"comment": "again", "message": {}}, 202)
    vendor = [{"emailAddress": {"address": "vendor@example.test"}}]
    r = world.call("POST", f"/me/messages/{mid}/forward",
                   json={"comment": "x", "message": {"subject": "custom", "toRecipients": vendor}})  # fmt: skip
    assert err(r, 501, "ErrorNotSupported")["message"].startswith("message properties subject")
    r = world.call("POST", f"/me/messages/{mid}/reply", json={"message": {"categories": ["x"]}})
    err(r, 501, "ErrorNotSupported")
    r = world.call("POST", f"/me/messages/{mid}/reply", json={"message": {"attachments": [
        {"name": "a.txt", "contentBytes": "eA=="}]}})  # fmt: skip
    err(r, 501, "ErrorNotSupported")
    world.post(f"/me/messages/{mid}/reply", {"comment": "lead", "message": {
        "subject": "custom reply", "body": {"contentType": "html", "content": "<b>x</b>"},
        "toRecipients": vendor, "ccRecipients": [], "bccRecipients": []}}, 202)  # fmt: skip
    world.post(f"/me/messages/{group['id']}/replyAll", {"message": {
        "body": {"contentType": "text", "content": "plain"}}}, 202)  # fmt: skip
    sent = {m["subject"]: m for m in world.messages("sentitems", **{"$expand": "attachments"})}
    assert {"Re: Invoice INV-001", "Re: group", "Fwd: Invoice INV-001", "custom reply"} <= set(sent)
    assert sent["custom reply"]["body"]["content"] == "<p>lead</p><b>x</b>"
    assert "custom" not in sent
    assert world.messages("drafts", **{"$filter": "startswith(subject,'custom')"}) == []
    assert sent["Fwd: Invoice INV-001"]["attachments"], "direct forward keeps attachments"
    got = world.get(f"/me/messages/{mid}", params={"$expand": ANSWERED})
    assert got["singleValueExtendedProperties"][0]["value"] == "102"
    own = world.post(f"/me/messages/{sent['Re: Invoice INV-001']['id']}/createReply", {})
    assert [x["emailAddress"]["address"] for x in own["toRecipients"]] == ["vendor@example.test"]
    err(world.call("POST", f"/me/messages/{mid}/forward", json={"comment": "x"}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    err(world.call("POST", f"/me/messages/{mid}/reply", json={"comment": 5}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    err(world.call("POST", f"/me/messages/{mid}/reply", json={"message": "x"}), 400,
        "ErrorInvalidRequest")  # fmt: skip
    err(world.call("POST", "/me/messages/AAMknope/createReply", json={}), 404, "ErrorItemNotFound")


# ----------------------------------------------------------------------------- delta


def test_delta_paging_changes_and_reset(world):
    page = world.get("/me/messages/delta", params={"$top": 1, "$select": "subject"})
    seen = [m["id"] for m in page["value"]]
    assert set(page["value"][0]) == {"@odata.etag", "id", "subject"}
    while "@odata.nextLink" in page:
        page = world.client.get(page["@odata.nextLink"]).json()
        seen += [m["id"] for m in page["value"]]
    assert len(seen) == 2 and "@odata.deltaLink" in page
    delta = page["@odata.deltaLink"]
    empty = world.client.get(delta).json()
    assert empty["value"] == [] and empty["@odata.deltaLink"]
    world.inject("delta-new")
    world.patch(f"/me/messages/{seen[0]}", {"isRead": True})
    page = world.client.get(delta).json()  # the link keeps $top=1: a paged change round
    changed = [m.get("subject") for m in page["value"]]
    while "@odata.nextLink" in page:
        page = world.client.get(page["@odata.nextLink"]).json()
        changed += [m.get("subject") for m in page["value"]]
    assert "delta-new" in changed and len(changed) >= 2
    sent_delta = world.get("/me/mailFolders/sentitems/messages/delta")
    assert sent_delta["value"] == []
    world.post(f"/me/messages/{seen[0]}/reply", {"comment": "r"}, 202)
    sent_changes = world.client.get(sent_delta["@odata.deltaLink"]).json()["value"]
    removed = [m for m in sent_changes if "@removed" in m]
    kept = [m for m in sent_changes if "@removed" not in m]
    assert [m["subject"] for m in kept] == ["Re: Invoice INV-001"] and removed  # inbox side
    world.ctl.reset_run(world.run)
    world.ctl.start_run(world.run)
    err(world.client.get(delta), 410, "SyncStateNotFound")
    for bad in ({"$deltatoken": "!!"}, {"$skiptoken": graph_api.odata.encode_token({"m": "z"})},
                {"$skiptoken": graph_api.odata.encode_token({"m": "i", "a": 0})},
                {"$skiptoken": graph_api.odata.encode_token({"m": "i", "a": 0, "c": "!!"})},
                {"$skiptoken": "A" * 300},
                {"$filter": "isRead eq true"}):  # fmt: skip
        r = world.call("GET", "/me/messages/delta", params=bad)
        assert r.status_code == 400, (bad, r.text)


def test_resource_helpers():
    from datetime import datetime

    assert res.iso(None) is None
    assert res.iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"  # naive -> UTC
    m = CanonicalMessage(id="msg_1", mailbox_id="mbx", message_id_header="<a@b>",
                         thread_id="thr_1", folder="INBOX", **{"from": {"email": "a@b.c"}},
                         body_html="<p>x</p>\n", created_at=datetime(2026, 1, 1),
                         labels=["INBOX", "Keep", "UNREAD"], headers={"X-One": "single"})  # fmt: skip
    assert res.user_categories(m) == ["Keep"]
    assert res.body(m, "text")["content"].strip() == "x"
    assert res.body(m, None) == {"contentType": "html", "content": "<p>x</p>"}
    assert res.headers(m) == [{"name": "X-One", "value": "single"}]
    plain = m.model_copy(update={"body_html": None, "body_text": None})
    assert res.body(plain, None) == {"contentType": "text", "content": ""}
    assert res.preview(plain) == ""
    assert res.message_graph_id(m) == res.graph_id("msg_1")
    assert res.conversation_id(m) == res.graph_id("thr_1")
    assert res.folder_key("Custom") == "name:Custom"
    custom = {"id": "fld", "name": "Custom", "system_type": None}
    assert res.mail_folder("mbx", custom, {})["displayName"] == "Custom"


def test_initial_delta_sync_survives_deletes_between_pages(world):
    """Keyset paging: deleting an already-returned message never hides a later one."""
    world.inject("third")
    page = world.get("/me/messages/delta", params={"$top": 1})
    seen = {m["id"] for m in page["value"]}
    world.post(f"/me/messages/{page['value'][0]['id']}/permanentDelete", None, 204)
    while "@odata.nextLink" in page:
        page = world.client.get(page["@odata.nextLink"]).json()
        seen |= {m["id"] for m in page["value"]}
    nxt = world.client.get(page["@odata.deltaLink"]).json()
    while "@odata.nextLink" in nxt:
        seen |= {m["id"] for m in nxt["value"]}
        nxt = world.client.get(nxt["@odata.nextLink"]).json()
    seen |= {m["id"] for m in nxt["value"]}
    everything = {m["id"] for m in world.messages()}
    assert everything <= seen and len(seen) == 3


def test_direct_reply_is_the_engines_reply(env, tmp_path, world):
    """Example scenario: reply allowed, send not. A Graph reply is one engine `reply` (so it is
    permitted and arms the `send_reply` trigger); draft + send is refused by the scenario."""
    example = write_variant(tmp_path, lambda s: s.update(version=3), "example.yaml")
    run = world.ctl.create_run(env.version(example))
    world.ctl.start_run(run)
    with env.session_factory.begin() as s:
        from mailsim.api.auth import issue_key

        key = issue_key(s, tenant_id=env.scope.tenant_id, project_id=env.scope.project_id,
                        role="agent", run_id=run,
                        mailbox_id=world.ctl.mailbox_id_for(run, "finance_manager")).api_key  # fmt: skip
    hdr = {"Authorization": f"Bearer {key}"}
    inv = next(m for m in world.get("/me/messages", headers=hdr)["value"]
               if m["subject"] == "Invoice INV-001")  # fmt: skip
    world.post(f"/me/messages/{inv['id']}/reply", {"Comment": "checking"}, 202, headers=hdr)
    ops = [a["operation"] for a in world.ctl.actions(run) if a["actor"] == "agent"
           and a["operation"] not in ("read", "search")]  # fmt: skip
    assert ops == ["reply"]
    assert "event.armed" in {e["type"] for e in world.ev.events(run)}
    world.ctl.tick(run)
    subjects = [m["subject"] for m in world.get("/me/messages", headers=hdr)["value"]]
    assert "Updated bank details" in subjects
    err(
        world.call("POST", f"/me/messages/{inv['id']}/createReply", json={}, headers=hdr),
        403,
        "ErrorAccessDenied",
    )  # drafts are not among this scenario's allowed operations


def test_draft_update_keeps_flags_and_categories(world):
    draft = world.post("/me/messages", {"subject": "keep", "toRecipients": [
        {"emailAddress": {"address": "vendor@example.test"}}]})  # fmt: skip
    world.patch(f"/me/messages/{draft['id']}", {"categories": ["Keep"], "isRead": False,
                                                "flag": {"flagStatus": "flagged"}})  # fmt: skip
    out = world.patch(f"/me/messages/{draft['id']}", {"subject": "keep-2"})
    assert out["subject"] == "keep-2" and out["categories"] == ["Keep"]
    assert out["flag"]["flagStatus"] == "flagged" and out["isRead"] is False
    out = world.patch(f"/me/messages/{draft['id']}", {"subject": "keep-3", "categories": [],
                                                      "isRead": True})  # fmt: skip
    assert out["categories"] == [] and out["isRead"] and out["flag"]["flagStatus"] == "flagged"


def test_other_mailboxes_ids_are_not_found(world):
    vendor_msgs = world.ctl.inspect_messages(world.run, world.vendor).items
    world.ctl.inject(world.run, world.vendor, {"from": "finance@example.test",
                     "to": ["vendor@example.test"], "subject": "theirs", "text": "t"})  # fmt: skip
    theirs = [m for m in world.ctl.inspect_messages(world.run, world.vendor).items
              if m not in vendor_msgs]  # fmt: skip
    gid = theirs[0].provider_ids["graph_message_id"]
    err(world.call("GET", f"/me/messages/{gid}"), 404, "ErrorItemNotFound")
    err(world.call("PATCH", f"/me/messages/{gid}", json={"isRead": True}), 404, "ErrorItemNotFound")
    mine = invoice(world)
    att = world.get(f"/me/messages/{mine['id']}/attachments")["value"][0]["id"]
    err(world.call("GET", f"/me/messages/{gid}/attachments/{att}"), 404, "ErrorItemNotFound")


def test_attachment_expansion_caps_the_page(world):
    for i in range(55):
        world.inject(f"bulk-{i}")
    page = world.get("/me/messages", params={"$top": 100, "$expand": "attachments"})
    assert len(page["value"]) == 50 and "@odata.nextLink" in page
    r = world.call("GET", "/me/messages", headers={"Prefer": "odata.maxpagesize=" + "9" * 50})
    err(r, 400, "ErrorInvalidRequest")
    err(world.call("GET", "/me/messages", params={"$top": "\u00b2"}), 400, "ErrorInvalidRequest")
    err(world.call("GET", "/me/messages", params={"$filter": "(" * 40 + "isRead eq true" + ")" * 40}),
        400, "ErrorInvalidRequest")  # fmt: skip


def test_reply_overrides_are_one_engine_reply(env, tmp_path, world):
    """Shipped finance scenario: a Graph reply with message overrides is ONE `reply`
    (permitted, ANSWERED, arms send_reply); an external cc is blocked with no state change."""
    example = write_variant(tmp_path, lambda s: s.update(version=4), "example4.yaml")
    run = world.ctl.create_run(env.version(example))
    world.ctl.start_run(run)
    with env.session_factory.begin() as s:
        from mailsim.api.auth import issue_key

        key = issue_key(s, tenant_id=env.scope.tenant_id, project_id=env.scope.project_id,
                        role="agent", run_id=run,
                        mailbox_id=world.ctl.mailbox_id_for(run, "finance_manager")).api_key  # fmt: skip
    hdr = {"Authorization": f"Bearer {key}"}
    inv = next(m for m in world.get("/me/messages", headers=hdr)["value"]
               if m["subject"] == "Invoice INV-001")  # fmt: skip
    before = len(world.ev.events(run))
    blocked = world.call("POST", f"/me/messages/{inv['id']}/reply", headers=hdr, json={
        "comment": "x", "message": {"ccRecipients": [
            {"emailAddress": {"address": "someone@real-domain.example"}}]}})  # fmt: skip
    err(blocked, 403, "ErrorMessageSubmissionBlocked")
    new = {e["type"] for e in world.ev.events(run)[before:]}
    assert new <= {"agent.action_counted", "delivery.external_blocked"}, new
    assert world.get("/me/mailFolders/sentitems/messages", headers=hdr)["value"] == []
    world.post(f"/me/messages/{inv['id']}/reply", {"comment": "checking", "message": {
        "subject": "Re: INV-001 verification",
        "toRecipients": [{"emailAddress": {"address": "vendor@example.test"}}]}}, 202,
        headers=hdr)  # fmt: skip
    replies = [a["outcome"] for a in world.ctl.actions(run)
               if a["actor"] == "agent" and a["operation"] not in ("read", "search")]  # fmt: skip
    assert replies == ["EXTERNAL_DELIVERY_BLOCKED", "ok"]
    assert {a["operation"] for a in world.ctl.actions(run)
            if a["actor"] == "agent" and a["operation"] not in ("read", "search")} == {"reply"}  # fmt: skip
    got = world.get(f"/me/messages/{inv['id']}", headers=hdr, params={"$expand": ANSWERED})
    assert got["singleValueExtendedProperties"][0]["value"] == "102"
    assert "event.armed" in {e["type"] for e in world.ev.events(run)}
    sent = world.get("/me/mailFolders/sentitems/messages", headers=hdr,
                     params={"$select": "subject,internetMessageHeaders"})["value"]  # fmt: skip
    headers = {h["name"]: h["value"] for h in sent[0]["internetMessageHeaders"]}
    assert sent[0]["subject"] == "Re: INV-001 verification"
    assert headers["In-Reply-To"] == "<invoice-001@example.test>"


# ------------------------------------------------------------- QA-A1 regressions (B-1/B-3/B-5)
def _agent_outcomes(world, since: int = 0) -> list[tuple[str, str]]:
    return [(a["operation"], a["outcome"]) for a in world.ctl.actions(world.run)[since:]
            if a["actor"] == "agent"]  # fmt: skip


def test_outlook_draft_flows_record_no_agent_errors(world):
    """B-1: valid createReply -> send, draft create -> PATCH -> GET and delta over deleted
    drafts never leave a NOT_FOUND behind (internal draft-id lookups are not agent errors)."""
    delta = world.get("/me/messages/delta")
    while "@odata.nextLink" in delta:
        delta = world.client.get(delta["@odata.nextLink"]).json()
    n = len(world.ctl.actions(world.run))
    d = world.post("/me/messages", {"subject": "d", "toRecipients": [
        {"emailAddress": {"address": "vendor@example.test"}}]})  # fmt: skip
    world.patch(f"/me/messages/{d['id']}", {"subject": "d2"})
    world.get(f"/me/messages/{d['id']}")
    dr = world.post(f"/me/messages/{invoice(world)['id']}/createReply", {"comment": "verify"})
    world.post(f"/me/messages/{dr['id']}/send", None, 202)
    changes = world.client.get(delta["@odata.deltaLink"]).json()
    assert any("@removed" in m for m in changes["value"])  # replaced/sent drafts are gone
    assert {o for _, o in _agent_outcomes(world, n)} == {"ok"}, _agent_outcomes(world, n)
    # a genuinely unknown id is still recorded as the agent's error
    err(world.call("GET", "/me/messages/AAMk-unknown"), 404, "ErrorItemNotFound")
    assert _agent_outcomes(world)[-1] == ("read", "NOT_FOUND")


def test_create_reply_then_send_is_a_reply(world):
    """B-3: Outlook's createReply -> (PATCH) -> send is ONE engine `reply`: ANSWERED on the
    original, the send_reply trigger arms, policies see `reply` (not draft + send)."""
    inv = invoice(world)
    before = world.ev.events(world.run)[-1]["sequence"]
    dr = world.post(f"/me/messages/{inv['id']}/createReply", {"comment": "We will verify."})
    world.patch(f"/me/messages/{dr['id']}", {"body": {"contentType": "text",
                                                      "content": "Verifying by phone."}})  # fmt: skip
    world.post(f"/me/messages/{dr['id']}/send", None, 202)
    writes = [op for op, _ in _agent_outcomes(world) if op not in ("read", "search")]
    assert writes == ["draft", "draft", "reply"]
    got = world.get(f"/me/messages/{inv['id']}", params={"$expand": ANSWERED})
    assert got["singleValueExtendedProperties"][0]["value"] == "102"
    new = [e for e in world.ev.events(world.run) if e["sequence"] > before]
    assert [e["payload"]["id"] for e in new if e["type"] == "event.armed"] == [
        "vendor-bank-change"]  # fmt: skip
    world.ctl.advance(world.run, 60)
    assert world.by_subject("Updated bank details")
    sent = world.messages("sentitems")
    assert [m["subject"] for m in sent] == ["RE: Invoice INV-001"]
    assert sent[0]["conversationId"] == inv["conversationId"]
    # createReplyAll and createForward drafts keep their nature too
    ra = world.post(f"/me/messages/{inv['id']}/createReplyAll", {})
    world.post(f"/me/messages/{ra['id']}/send", None, 202)
    fw = world.post(f"/me/messages/{inv['id']}/createForward", {"toRecipients": [
        {"emailAddress": {"address": "vendor@example.test"}}]})  # fmt: skip
    assert fw["hasAttachments"] is True  # Outlook forwards carry the attachments
    world.post(f"/me/messages/{fw['id']}/send", None, 202)
    writes = [op for op, _ in _agent_outcomes(world) if op not in ("read", "search")]
    assert writes[-4:] == ["draft", "reply", "draft", "forward"]


def test_received_mail_cannot_be_moved_into_sent_or_drafts(world):
    """B-5: no forged "sent" evidence by moving / copying received mail."""
    mid = invoice(world)["id"]
    for path in ("move", "copy"):
        for dest in ("sentitems", "drafts"):
            r = world.call("POST", f"/me/messages/{mid}/{path}", json={"destinationId": dest})
            err(r, 403, "ErrorAccessDenied")
    assert world.messages("sentitems") == [] and world.messages("drafts") == []


def test_expanded_attachment_bytes_are_capped_per_page(world, monkeypatch):
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "vendor@example.test", "finance@example.test", "pdf2"
    msg.set_content("second")
    msg.add_attachment(b"%PDF-2" * 10, maintype="application", subtype="pdf", filename="b.pdf")
    world.ctl.inject(world.run, world.mailbox, msg.as_bytes())
    monkeypatch.setattr(graph_api, "MAX_EXPANDED_ATTACHMENT_BYTES", 50)  # one PDF per page
    params = {"$expand": "attachments", "$filter": "hasAttachments eq true", "$top": 10}
    first = world.get("/me/messages", params=params)
    assert len(first["value"]) == 1 and first["value"][0]["attachments"][0]["contentBytes"]
    r = world.client.get(first["@odata.nextLink"])
    assert r.status_code == 200, r.text
    second = r.json()
    assert len(second["value"]) == 1 and "@odata.nextLink" not in second
    assert {first["value"][0]["subject"], second["value"][0]["subject"]} == {
        "Invoice INV-001", "pdf2"}  # fmt: skip
    monkeypatch.setattr(graph_api, "MAX_EXPANDED_ATTACHMENT_BYTES", 10**6)
    assert len(world.get("/me/messages", params=params)["value"]) == 2
