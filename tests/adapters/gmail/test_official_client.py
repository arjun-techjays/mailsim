"""Fidelity: the official google-api-python-client (static Gmail v1 discovery document) against
the sandbox, served by a real uvicorn on 127.0.0.1 (loopback only).

The client is pointed at the sandbox with ``client_options={"api_endpoint": ...}`` and plain
OAuth2 bearer credentials whose token is the sandbox agent key. googleapiclient always builds
media-upload URLs with ``https://`` (it swaps only the host of the discovery rootUrl), so for a
plain-HTTP sandbox the transport rewrites that scheme back for the loopback host (documented in
docs/compat/gmail.md).
"""

from __future__ import annotations

import base64
import contextlib
import json
import socket
import threading
import time
from email.message import EmailMessage

import httplib2
import pytest
import uvicorn
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from tests.adapters.gmail.conftest import INVOICE, PO, Gm

pytestmark = pytest.mark.db


@contextlib.contextmanager
def serve(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))  # fmt: skip
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


class LoopbackHttp(httplib2.Http):
    """Rewrite https://127.0.0.1 (upload URLs) to http://; refuse every other host."""

    def request(self, uri, *args, **kwargs):
        uri = uri.replace("https://127.0.0.1:", "http://127.0.0.1:", 1)
        assert uri.startswith("http://127.0.0.1:"), f"non-loopback request: {uri}"
        return super().request(uri, *args, **kwargs)


def gmail_client(url: str, token: str):
    http = AuthorizedHttp(Credentials(token=token), http=LoopbackHttp())
    return build("gmail", "v1", http=http, client_options={"api_endpoint": url},
                 static_discovery=True, cache_discovery=False)  # fmt: skip


@pytest.fixture()
def live(env, tmp_path):
    gm = Gm(env, tmp_path)
    with serve(gm.app) as url:
        service = gmail_client(url, gm.key)
        gm.url = url
        yield gm, service.users()


def raw(to: str, subject: str, text: str = "hi", **headers: str) -> str:
    em = EmailMessage()
    em["To"], em["Subject"] = to, subject
    for k, v in headers.items():
        em[k] = v
    em.set_content(text)
    return base64.urlsafe_b64encode(em.as_bytes()).decode()


def reason(exc: HttpError) -> tuple[int, str]:
    body = json.loads(exc.content)["error"]
    return exc.resp.status, body["details"][0]["reason"]


def test_profile_list_get_and_paging(live):
    gm, users = live
    prof = users.getProfile(userId="me").execute()
    assert prof["emailAddress"] == "finance@example.test" and prof["messagesTotal"] == 2
    seen, request = [], users.messages().list(userId="me", maxResults=1)
    while request is not None:
        page = request.execute()
        seen += [m["id"] for m in page.get("messages", [])]
        request = users.messages().list_next(request, page)
    assert seen == [gm.gid(PO), gm.gid(INVOICE)]
    full = users.messages().get(userId="me", id=gm.gid(INVOICE)).execute()
    pdf = full["payload"]["parts"][1]
    att = users.messages().attachments().get(userId="me", messageId=full["id"],
                                             id=pdf["body"]["attachmentId"]).execute()  # fmt: skip
    assert base64.urlsafe_b64decode(att["data"]).startswith(b"%PDF")
    raw_msg = users.messages().get(userId="me", id=full["id"], format="raw").execute()
    assert b"invoice-001@example.test" in base64.urlsafe_b64decode(raw_msg["raw"])
    meta = users.messages().get(userId="me", id=full["id"], format="metadata",
                                metadataHeaders=["Subject"], fields="payload/headers").execute()  # fmt: skip
    assert meta == {"payload": {"headers": [{"name": "Subject", "value": INVOICE}]}}
    hits = users.messages().list(userId="me", q='subject:"INV-001" has:attachment').execute()
    assert [m["id"] for m in hits["messages"]] == [gm.gid(INVOICE)]


def test_send_reply_modify_trash_delete(live):
    gm, users = live
    inv = users.messages().get(userId="me", id=gm.gid(INVOICE), format="metadata").execute()
    reply = users.messages().send(userId="me", body={
        "threadId": inv["threadId"],
        "raw": raw("vendor@example.test", "Re: Invoice INV-001", "ack",
                   **{"In-Reply-To": "<invoice-001@example.test>"})}).execute()  # fmt: skip
    assert reply["threadId"] == inv["threadId"] and reply["labelIds"] == ["SENT"]
    assert ("reply", "ok") in gm.actions()
    media = users.messages().send(userId="me", media_body=MediaInMemoryUpload(
        base64.urlsafe_b64decode(raw("vendor@example.test", "via upload")),
        mimetype="message/rfc822")).execute()  # fmt: skip
    assert media["labelIds"] == ["SENT"]
    mod = users.messages().modify(userId="me", id=inv["id"], body={
        "removeLabelIds": ["UNREAD", "INBOX"], "addLabelIds": ["STARRED"]}).execute()  # fmt: skip
    assert set(mod["labelIds"]) == {"STARRED", "ANSWERED"}
    assert users.messages().trash(userId="me", id=inv["id"]).execute()["labelIds"][0] == "TRASH"
    users.messages().untrash(userId="me", id=inv["id"]).execute()
    users.messages().batchModify(userId="me", body={"ids": [inv["id"]],
                                                     "addLabelIds": ["INBOX"]}).execute()  # fmt: skip
    users.messages().delete(userId="me", id=inv["id"]).execute()
    with pytest.raises(HttpError) as ei:
        users.messages().get(userId="me", id=inv["id"]).execute()
    assert reason(ei.value) == (404, "NOT_FOUND")
    with pytest.raises(HttpError) as ei:
        users.messages().send(userId="me", body={"raw": raw("x@real.example", "escape")}).execute()
    assert reason(ei.value) == (400, "EXTERNAL_DELIVERY_BLOCKED")
    with pytest.raises(HttpError) as ei:
        users.messages().list(userId="me", q="larger:10M").execute()
    assert reason(ei.value) == (400, "INVALID_REQUEST")
    inserted = users.messages().insert(userId="me", body={
        "raw": base64.urlsafe_b64encode(b"From: vendor@example.test\r\nSubject: ins\r\n\r\nx").decode(),
        "labelIds": ["INBOX", "UNREAD"]}).execute()  # fmt: skip
    assert set(inserted["labelIds"]) == {"INBOX", "UNREAD"}


def test_labels_drafts_threads_history(live):
    gm, users = live
    start = users.getProfile(userId="me").execute()["historyId"]
    label = users.labels().create(userId="me", body={"name": "Client"}).execute()
    assert label["id"] in {x["id"] for x in users.labels().list(userId="me").execute()["labels"]}
    users.messages().modify(
        userId="me", id=gm.gid(PO), body={"addLabelIds": [label["id"]]}
    ).execute()
    assert users.labels().get(userId="me", id=label["id"]).execute()["messagesTotal"] == 1
    renamed = (
        users.labels().patch(userId="me", id=label["id"], body={"name": "Client/Renamed"}).execute()
    )
    users.labels().delete(userId="me", id=renamed["id"]).execute()

    draft = users.drafts().create(userId="me", body={"message": {
        "raw": raw("vendor@example.test", "draft")}}).execute()  # fmt: skip
    assert users.drafts().get(userId="me", id=draft["id"]).execute()["id"] == draft["id"]
    assert [d["id"] for d in users.drafts().list(userId="me").execute()["drafts"]] == [draft["id"]]
    users.drafts().update(userId="me", id=draft["id"], body={"message": {
        "raw": raw("vendor@example.test", "draft v2")}}).execute()  # fmt: skip
    sent = users.drafts().send(userId="me", body={"id": draft["id"]}).execute()
    assert sent["labelIds"] == ["SENT"]
    other = users.drafts().create(userId="me", body={"message": {
        "raw": raw("vendor@example.test", "bye")}}).execute()  # fmt: skip
    users.drafts().delete(userId="me", id=other["id"]).execute()

    threads = users.threads().list(userId="me", q="invoice").execute()["threads"]
    thread = users.threads().get(userId="me", id=threads[0]["id"], format="minimal").execute()
    assert len(thread["messages"]) == 2
    users.threads().modify(
        userId="me", id=thread["id"], body={"addLabelIds": ["STARRED"]}
    ).execute()

    history = users.history().list(userId="me", startHistoryId=start,
                                   historyTypes=["messageAdded"]).execute()  # fmt: skip
    added = [r["messagesAdded"][0]["message"]["id"] for r in history["history"]]
    assert sent["id"] in added
    gm.ctl.reset_run(gm.run)
    gm.ctl.start_run(gm.run)
    with pytest.raises(HttpError) as ei:
        users.history().list(userId="me", startHistoryId=start).execute()
    assert reason(ei.value) == (404, "NOT_FOUND")


def test_unsupported_and_auth_errors(live):
    gm, users = live
    with pytest.raises(HttpError) as ei:
        users.settings().getImap(userId="me").execute()
    assert reason(ei.value) == (501, "UNSUPPORTED_OPERATION")
    anonymous = build("gmail", "v1", http=LoopbackHttp(), client_options={"api_endpoint": gm.url},
                      static_discovery=True, cache_discovery=False)  # fmt: skip
    with pytest.raises(HttpError) as ei:
        anonymous.users().messages().list(userId="me").execute()
    assert reason(ei.value) == (401, "UNAUTHENTICATED")
    # with OAuth credentials the client reacts to 401 by trying to refresh the token
    bad = gmail_client(gm.url, "msk_000000000000_" + "x" * 40)
    with pytest.raises(RefreshError):
        bad.users().messages().list(userId="me").execute()
    with pytest.raises(HttpError) as ei:
        users.getProfile(userId="someone@else.example").execute()
    assert reason(ei.value) == (403, "PERMISSION_DENIED")
