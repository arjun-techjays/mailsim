"""Gmail adapter fixtures: a real app (REST + Gmail router) over real PostgreSQL, one run with an
agent key per test."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailsim.api.app import create_app
from mailsim.api.auth import issue_key
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.serve import mount_adapters
from tests.scenarios import write_variant

BASE = "/gmail/v1/users/me"


def open_agent(s: dict[str, Any]) -> None:
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500
    s["evaluation"]["limits"]["max_virtual_duration_seconds"] = 30 * 86400


def raw_message(headers: dict[str, str], text: str = "hello", html: str | None = None,
                attachment: bytes | None = None) -> bytes:  # fmt: skip
    em = EmailMessage()
    for k, v in headers.items():
        em[k] = v
    em.set_content(text)
    if html is not None:
        em.add_alternative(html, subtype="html")
    if attachment is not None:
        em.add_attachment(attachment, maintype="application", subtype="pdf", filename="a.pdf")
    return em.as_bytes()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


class Gm:
    """One started run of the example scenario + an agent key for its agent mailbox."""

    def __init__(self, env, tmp_path, mutate=open_agent) -> None:
        self.env = env
        self.ctl = Controller(env.engine, env.scope)
        self.ev = EvaluatorView(env.engine, env.scope)
        self.run = self.ctl.create_run(env.version(write_variant(tmp_path, mutate)))
        self.ctl.start_run(self.run)
        self.mailbox_id = self.ctl.mailbox_id_for(self.run, "finance_manager")
        self.vendor_mb = self.ctl.mailbox_id_for(self.run, "vendor")
        self.app = create_app(engine=env.engine, session_factory=env.session_factory,
                              blobs=env.blobs)  # fmt: skip
        mount_adapters(self.app, ["mailsim.adapters.gmail.api"])
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.key = self.issue(self.mailbox_id)
        self.client.headers["Authorization"] = f"Bearer {self.key}"

    def issue(self, mailbox_id: str | None = None, role: str = "agent") -> str:
        with self.env.session_factory.begin() as s:
            return issue_key(s, tenant_id=self.env.scope.tenant_id,
                             project_id=self.env.scope.project_id, role=role,
                             run_id=self.run if role == "agent" else None,
                             mailbox_id=mailbox_id if role == "agent" else None).api_key  # fmt: skip

    @property
    def mb(self) -> AgentMailbox:
        return AgentMailbox(self.env.engine, self.env.scope, self.run, self.mailbox_id)

    def call(self, method: str, path: str, expect: int | None = 200, base: str = BASE, **kw: Any):
        r = self.client.request(method, base + path, **kw)
        if expect is not None:
            assert r.status_code == expect, (method, path, r.status_code, r.text)
        return r

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw).json()

    def post(self, path: str, json: Any = None, **kw: Any) -> Any:
        r = self.call("POST", path, json=json, **kw)
        return r.json() if r.content else None

    def ids(self, **params: Any) -> list[str]:
        return [m["id"] for m in self.get("/messages", params=params).get("messages", [])]

    def gid(self, subject: str) -> str:
        """Gmail id of the (first) message with this subject, incl. trash."""
        for m in self.mb.list_messages(page_size=500).items:
            if m.subject == subject:
                return m.provider_ids["gmail_message_id"]
        raise AssertionError(subject)

    def send(self, to: str, subject: str, text: str = "hello", **headers: str) -> dict[str, Any]:
        raw = raw_message({"To": to, "Subject": subject, **headers}, text)
        return self.post("/messages/send", json={"raw": b64(raw)})

    def error(self, r) -> tuple[int, str, str]:
        e = r.json()["error"]
        return e["code"], e["details"][0]["reason"], e["errors"][0]["reason"]

    def inject(self, text: str = "x", **headers: str) -> str:
        return self.ctl.inject(self.run, self.mailbox_id, raw_message(headers, text))["message_id"]

    def actions(self) -> list[tuple[str, str]]:
        return [(a["operation"], a["outcome"]) for a in self.ctl.actions(self.run)]


@pytest.fixture()
def gm(env, tmp_path) -> Gm:
    return Gm(env, tmp_path)


INVOICE = "Invoice INV-001"
PO = "Re: Invoice INV-001"
