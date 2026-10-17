"""An agent connects to a generated leadership mailbox (the committed CEO pack) through EVERY
interface - REST, Gmail (google-api-python-client), Graph (msgraph-sdk), IMAP/SMTP (imaplib /
smtplib), MCP over streamable HTTP and MCP over stdio (official MCP client) - and lists,
reads, searches and replies, with results consistent across interfaces."""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from email.message import EmailMessage
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder as FolderMessages,
)
from msgraph.generated.users.item.messages.item.reply.reply_post_request_body import (
    ReplyPostRequestBody,
)
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder

from mailsim.api.app import create_app
from mailsim.api.auth import issue_key
from mailsim.engine.facades import AgentMailbox, EvaluatorView
from mailsim.serve import mount_adapters
from mcp import Client, StdioServerParameters
from tests.adapters.gmail.test_official_client import gmail_client, serve
from tests.adapters.graph.test_sdk import sdk
from tests.contract.drivers import ImapSmtpDriver, McpDriver
from tests.scenarios import ROOT

pytestmark = [pytest.mark.db, pytest.mark.filterwarnings("ignore::DeprecationWarning")]
CEO = ROOT / "scenarios" / "leadership" / "ceo" / "scenario.yaml"
QUERY = "Larkspur"


class Stack:
    def __init__(self, env: Any) -> None:
        self.env = env
        self.run = env.run(CEO)
        self.mailbox = env.mbx(self.run, "ceo")
        self.mb = AgentMailbox(env.engine, env.scope, self.run, self.mailbox)
        with env.session_factory.begin() as s:
            self.key = issue_key(s, tenant_id=env.scope.tenant_id,
                                 project_id=env.scope.project_id, role="agent", run_id=self.run,
                                 mailbox_id=self.mailbox).api_key  # fmt: skip
        self.app = create_app(engine=env.engine, session_factory=env.session_factory,
                              blobs=env.blobs)  # fmt: skip
        mount_adapters(self.app)

    def canonical(self, folder: str = "inbox") -> dict[str, str]:
        """Message-ID header -> subject, from the engine facade."""
        items = self.mb.list_messages(folder=folder, page_size=500).items
        return {m.message_id_header: m.subject for m in items}

    def canonical_search(self) -> set[str]:
        return {m.message_id_header for m in self.mb.search_messages(QUERY)}


@pytest.fixture()
def stack(env) -> Stack:
    return Stack(env)


def _rest(st: Stack) -> dict[str, Any]:
    http = TestClient(st.app)
    http.headers["Authorization"] = f"Bearer {st.key}"
    base = f"/v1/mailboxes/{st.mailbox}"
    inbox = http.get(f"{base}/messages", params={"folder": "inbox", "page_size": 500}).json()
    hits = http.get(f"{base}/messages", params={"q": QUERY, "page_size": 500}).json()
    first = inbox["items"][0]
    body = http.get(f"{base}/messages/{first['id']}").json()
    r = http.post(f"{base}/messages/{first['id']}/reply", json={"text": "Ack via REST"})
    assert r.status_code < 300, r.text
    return {"inbox": {m["message_id_header"] for m in inbox["items"]},
            "search": {m["message_id_header"] for m in hits["items"]},
            "read": (first["message_id_header"], body["subject"], body["body_text"])}  # fmt: skip


def _gmail(st: Stack) -> dict[str, Any]:
    with serve(st.app) as url:
        users = gmail_client(url, st.key).users()

        def header(gid: str) -> str:
            m = users.messages().get(userId="me", id=gid, format="metadata",
                                     metadataHeaders=["Message-ID"]).execute()  # fmt: skip
            return next(h["value"] for h in m["payload"]["headers"] if h["name"] == "Message-ID")

        listed = users.messages().list(userId="me", labelIds=["INBOX"], maxResults=500).execute()
        ids = [m["id"] for m in listed["messages"]]
        hits = users.messages().list(userId="me", q=QUERY, maxResults=500).execute()
        first = users.messages().get(userId="me", id=ids[0], format="full").execute()
        hdrs = {h["name"]: h["value"] for h in first["payload"]["headers"]}
        reply = EmailMessage()
        reply["To"], reply["Subject"] = hdrs["From"], "Re: " + hdrs["Subject"]
        reply["In-Reply-To"], reply["References"] = hdrs["Message-ID"], hdrs["Message-ID"]
        reply.set_content("Ack via Gmail")
        raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
        users.messages().send(
            userId="me", body={"raw": raw, "threadId": first["threadId"]}
        ).execute()
        return {"inbox": {header(g) for g in ids},
                "search": {header(m["id"]) for m in hits.get("messages", [])},
                "read": (hdrs["Message-ID"], hdrs["Subject"], first["snippet"])}  # fmt: skip


def _graph(st: Stack) -> dict[str, Any]:
    client = sdk(st.app, st.key)

    async def go() -> dict[str, Any]:
        from kiota_abstractions.base_request_configuration import RequestConfiguration

        q = FolderMessages.MessagesRequestBuilderGetQueryParameters(top=500)
        inbox = await client.me.mail_folders.by_mail_folder_id("inbox").messages.get(
            request_configuration=RequestConfiguration(query_parameters=q))  # fmt: skip
        sq = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
            search=f'"{QUERY}"', top=500)  # fmt: skip
        hits = await client.me.messages.get(
            request_configuration=RequestConfiguration(query_parameters=sq))  # fmt: skip
        first = inbox.value[0]
        full = await client.me.messages.by_message_id(first.id).get()
        await client.me.messages.by_message_id(first.id).reply.post(
            ReplyPostRequestBody(comment="Ack via Graph"))  # fmt: skip
        return {"inbox": {m.internet_message_id for m in inbox.value},
                "search": {m.internet_message_id for m in hits.value},
                "read": (full.internet_message_id, full.subject, full.body.content)}  # fmt: skip

    return asyncio.run(go())


def _imap_smtp(st: Stack) -> dict[str, Any]:
    d = ImapSmtpDriver(st.mb)
    inbox = d.list("inbox")
    first = d.get(inbox[0].ref)
    d.reply(first.ref, "Ack via IMAP/SMTP")
    return {"inbox": {m.message_id_header for m in inbox},
            "search": {m.message_id_header for m in d.search(QUERY)},
            "read": (first.message_id_header, first.subject, first.body)}  # fmt: skip


def _mcp_http(st: Stack) -> dict[str, Any]:
    d = McpDriver(st.mb)
    inbox = d.list("inbox")
    first = d.get(inbox[0].ref)
    d.reply(first.ref, "Ack via MCP (HTTP)")
    return {"inbox": {m.message_id_header for m in inbox},
            "search": {m.message_id_header for m in d.search(QUERY)},
            "read": (first.message_id_header, first.subject, first.body)}  # fmt: skip


def _mcp_stdio(st: Stack) -> dict[str, Any]:
    url = st.env.session_factory.kw["bind"].url.render_as_string(hide_password=False)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
           "MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(st.env.blobs.root),
           "MAILSIM_API_KEY": st.key}  # fmt: skip
    params = StdioServerParameters(command=sys.executable, args=["-m", "tests.mcp.stdio_entry"],
                                   env=env, cwd=str(ROOT))  # fmt: skip

    async def go() -> dict[str, Any]:
        async with Client(params) as client:
            inbox = (await client.call_tool("list_messages", {"folder": "inbox",
                                                              "page_size": 500})).structured_content  # fmt: skip
            hits = (await client.call_tool("search_messages", {"query": QUERY,
                                                               "page_size": 500})).structured_content  # fmt: skip
            first = inbox["items"][0]
            got = (
                await client.call_tool("get_message", {"message_id": first["id"]})
            ).structured_content
            sent = await client.call_tool("reply_to_message", {"message_id": first["id"],
                                                               "text": "Ack via MCP (stdio)"})  # fmt: skip
            assert not sent.is_error, sent.structured_content
            return {"inbox": {m["message_id_header"] for m in inbox["items"]},
                    "search": {m["message_id_header"] for m in hits["items"]},
                    "read": (got["message_id_header"], got["subject"], got["body_text"])}  # fmt: skip

    return anyio.run(go)


INTERFACES = {"rest": _rest, "gmail": _gmail, "graph": _graph, "imap_smtp": _imap_smtp,
              "mcp_http": _mcp_http, "mcp_stdio": _mcp_stdio}  # fmt: skip


def test_every_interface_sees_the_same_leadership_mailbox(stack):
    inbox = stack.canonical()
    search = stack.canonical_search() & set(inbox)
    assert len(inbox) >= 30 and len(search) >= 2
    before_sent = len(stack.canonical("sent"))
    results = {name: fn(stack) for name, fn in INTERFACES.items()}
    for name, res in results.items():
        assert res["inbox"] == set(inbox), name
        # searches may also hit the agent's own replies (Sent): compare the inbox hits
        assert res["search"] & set(inbox) == search, name
        header, subject, body = res["read"]
        assert subject == inbox[header] and body, name
    sent = EvaluatorView(stack.env.engine, stack.env.scope).state(stack.run)["messages"]
    replies = [m for m in sent if m["mailbox_id"] == stack.mailbox and m["in_reply_to"]
               and m["message_id_header"] not in inbox]  # fmt: skip
    assert len(stack.canonical("sent")) == before_sent + len(INTERFACES)
    assert {m["in_reply_to"] for m in replies} <= set(inbox)
    interfaces = {a["interface"] for a in stack.env.engine.actions(stack.env.scope, stack.run)
                  if a["actor"] == "agent"}  # fmt: skip
    assert interfaces >= {"rest", "gmail", "graph", "imap", "smtp", "mcp_http", "mcp_stdio"}
