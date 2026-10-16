"""Nothing an agent can read says it is in a simulation: banners, MCP identity, instructions
and tool catalogue, and the error texts of the external-delivery and permission paths, for
every agent interface (REST, Gmail, Graph, MCP, IMAP, SMTP)."""

from __future__ import annotations

import base64
import json
import re
import socket
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailsim.api.app import create_app
from mailsim.serve import mount_adapters
from tests.protocols.conftest import AGENT, open_agent

pytestmark = pytest.mark.db

DENYLIST = re.compile(r"simulat|sandbox|mailsim|sink|event_only", re.IGNORECASE)
EXTERNAL = "ceo@real-company.example"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}  # fmt: skip


def restricted(s: dict[str, Any]) -> None:
    open_agent(s)
    s["version"] = 2
    s["agent"]["allowed_operations"] = ["read", "search"]


def raw_message(to: str, *, attach: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = AGENT, to, "hello"
    msg.set_content("text")
    if attach:
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="a.pdf")
    return msg.as_bytes()


def banner(port: int) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        return sock.recv(4096).decode()


class Http:
    def __init__(self, env: Any, key: str, mailbox_id: str) -> None:
        app = create_app(engine=env.engine, session_factory=env.session_factory, blobs=env.blobs)
        mount_adapters(app)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.headers["Authorization"] = f"Bearer {key}"
        self.mbx = mailbox_id

    def mcp(self, method: str, params: dict[str, Any] | None = None) -> str:
        body = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}
        r = self.client.post("/mcp", json=INIT if method == "initialize" else body,
                             headers=MCP_HEADERS)  # fmt: skip
        assert r.status_code == 200, r.text
        return r.text

    def sends(self, to: str) -> list[str]:
        """The response texts of a send to `to` through REST, Gmail, Graph and MCP."""
        c = self.client
        raw = base64.urlsafe_b64encode(raw_message(to)).decode()
        graph = {"message": {"subject": "s", "body": {"contentType": "text", "content": "x"},
                             "toRecipients": [{"emailAddress": {"address": to}}]}}  # fmt: skip
        out = [
            c.post(f"/v1/mailboxes/{self.mbx}/messages", json={"to": [to], "subject": "s"}),
            c.post("/gmail/v1/users/me/messages/send", json={"raw": raw}),
            c.post("/graph/v1.0/me/sendMail", json=graph),
        ]
        assert all(r.status_code >= 400 for r in out), [r.text for r in out]
        texts = [r.text for r in out]
        mcp = self.mcp("tools/call", {"name": "send_message",
                                      "arguments": {"to": [to], "subject": "s"}})  # fmt: skip
        assert '"isError":true' in mcp.replace(" ", "")
        return [*texts, mcp]


def smtp_texts(port: int, key: str, *, to: str, attach: bool = False) -> list[str]:
    import smtplib

    with smtplib.SMTP("127.0.0.1", port, timeout=10) as s:
        texts = [s.ehlo("tests.local")[1].decode()]
        s.login(AGENT, key)
        s.mail(AGENT)
        code, text = s.rcpt(to)
        texts.append(text.decode())
        if code == 250:
            texts.append(s.data(raw_message(to, attach=attach))[1].decode())
    return texts


def test_agent_facing_texts_never_reveal_the_simulation(env, make_proto):
    open_run = make_proto()
    limited = make_proto(restricted)
    seen: dict[str, str] = {}

    # SMTP / IMAP banners, ID and LOGOUT
    seen["smtp banner"] = banner(open_run.servers.smtp_port)
    seen["imap banner"] = banner(open_run.servers.imap_port)
    raw = open_run.raw()
    raw.cmd(f"a LOGIN {AGENT} {open_run.key}".encode())
    seen["imap id"] = b"".join(raw.cmd(b'b ID ("name" "t")')).decode()
    seen["imap logout"] = b"".join(raw.cmd(b"c LOGOUT")).decode()
    # SMTP: external recipient refused, attachment refused, permission refused
    seen["smtp external"] = " | ".join(
        smtp_texts(open_run.servers.smtp_port, open_run.key, to=EXTERNAL)
    )
    seen["smtp attachment"] = " | ".join(
        smtp_texts(open_run.servers.smtp_port, open_run.key, to="vendor@example.test", attach=True)
    )
    seen["smtp permission"] = " | ".join(
        smtp_texts(limited.servers.smtp_port, limited.key, to="vendor@example.test")
    )
    assert "550" in seen["smtp permission"] or "PERMISSION_DENIED" in seen["smtp permission"]
    # IMAP permission refusal
    lim = limited.raw()
    lim.cmd(f"a LOGIN {AGENT} {limited.key}".encode())
    lim.cmd(b"b SELECT INBOX")
    seen["imap permission"] = b"".join(lim.cmd(b"c STORE 1 +FLAGS (\\Flagged)")).decode()
    assert "PERMISSION_DENIED" in seen["imap permission"]

    # HTTP interfaces: external-delivery refusal (open run) and permission refusal (limited)
    http = Http(env, open_run.key, open_run.mailbox_id)
    seen["mcp initialize"] = http.mcp("initialize")
    assert json.loads(seen["mcp initialize"])["result"]["instructions"]
    seen["mcp tools/list"] = http.mcp("tools/list")
    for i, text in enumerate(http.sends(EXTERNAL)):
        seen[f"external send {i}"] = text
    for i, text in enumerate(
        Http(env, limited.key, limited.mailbox_id).sends("vendor@example.test")
    ):
        seen[f"permission send {i}"] = text
    bad = TestClient(http.client.app, raise_server_exceptions=False)
    for path in ("/gmail/v1/users/me/profile", "/graph/v1.0/me/messages"):
        r = bad.get(path, headers={"Authorization": "Bearer nope"})
        seen[f"401 {path}"] = f"{r.headers.get('www-authenticate', '')} {r.text}"

    leaks = {name: DENYLIST.findall(text) for name, text in seen.items() if DENYLIST.search(text)}
    assert leaks == {}
