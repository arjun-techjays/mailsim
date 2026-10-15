"""IMAP/SMTP listener fixtures: real listeners on loopback (random free ports) over a real
engine + PostgreSQL. Tests talk to them with the stdlib clients (imaplib / smtplib)."""

from __future__ import annotations

import contextlib
import imaplib
import re
import smtplib
import socket
import ssl
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from mailsim.api.auth import issue_key
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.protocols.server import ProtocolServers
from tests.scenarios import write_variant

pytestmark = pytest.mark.db

AGENT = "finance@example.test"
VENDOR = "vendor@example.test"


def open_agent(s: dict[str, Any]) -> None:
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500


@dataclass
class Proto:
    env: Any
    servers: ProtocolServers
    run: str
    ctl: Controller
    ev: EvaluatorView
    mailbox_id: str
    key: str
    opened: list[Any] = field(default_factory=list)

    @property
    def mailbox(self) -> AgentMailbox:
        return AgentMailbox(self.env.engine, self.env.scope, self.run, self.mailbox_id)

    def key_for(self, persona: str = "finance_manager", **kw: Any) -> str:
        mbx = self.ctl.mailbox_id_for(self.run, persona)
        with self.env.session_factory.begin() as s:
            return issue_key(s, tenant_id=self.env.scope.tenant_id,
                             project_id=self.env.scope.project_id, role=kw.pop("role", "agent"),
                             run_id=self.run, mailbox_id=mbx, **kw).api_key  # fmt: skip

    def imap(self, *, login: bool = True) -> imaplib.IMAP4:
        client = imaplib.IMAP4("127.0.0.1", self.servers.imap_port, timeout=10)
        self.opened.append(client)
        if login:
            client.login(AGENT, self.key)
        return client

    def smtp(self, *, login: bool = True) -> smtplib.SMTP:
        client = smtplib.SMTP("127.0.0.1", self.servers.smtp_port, timeout=10)
        self.opened.append(client)
        client.ehlo("tests.local")
        if login:
            client.login(AGENT, self.key)
        return client

    def raw(self) -> RawClient:
        client = RawClient(self.servers.imap_port)
        self.opened.append(client)
        return client

    def actions(self) -> list[tuple[str, str, str]]:
        return [(a["actor"], a["operation"], a["outcome"]) for a in self.ctl.actions(self.run)]


class RawClient:
    """Byte-level IMAP client for transcript tests."""

    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.buf = b""
        self.greeting = self.line()

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def line(self) -> bytes:
        while b"\r\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                out, self.buf = self.buf, b""
                return out
            self.buf += chunk
        out, _, self.buf = self.buf.partition(b"\r\n")
        return out + b"\r\n"

    def until(self, tag: bytes) -> list[bytes]:
        """Read response lines (literals inline) until the tagged completion for `tag`."""
        lines: list[bytes] = []
        while True:
            line = self.line()
            if not line:
                return lines
            while (m := re.search(rb"\{(\d+)\}\r\n$", line)) is not None:
                size = int(m.group(1))
                while len(self.buf) < size:
                    self.buf += self.sock.recv(65536)
                line += self.buf[:size]
                self.buf = self.buf[size:]
                line += self.line()
            lines.append(line)
            if line.startswith(tag + b" "):
                return lines

    def cmd(self, data: bytes) -> list[bytes]:
        tag = data.split(b" ", 1)[0]
        self.send(data + b"\r\n")
        return self.until(tag)

    def close(self) -> None:
        self.sock.close()


@pytest.fixture()
def make_proto(env, tmp_path) -> Iterator[Callable[..., Proto]]:
    made: list[Proto] = []

    def build(mutate: Callable[[dict[str, Any]], None] | None = open_agent, *,
              tls: bool = False, start: bool = True, **server_kw: Any) -> Proto:  # fmt: skip
        ctl, ev = Controller(env.engine, env.scope), EvaluatorView(env.engine, env.scope)
        run = ctl.create_run(env.version(write_variant(tmp_path, mutate)))
        if start:
            ctl.start_run(run)
        servers = ProtocolServers(env.engine, imap_port=0, smtp_port=0, tls=tls,
                                  **server_kw).start()  # fmt: skip
        proto = Proto(env, servers, run, ctl, ev, ctl.mailbox_id_for(run, "finance_manager"), "")
        proto.key = proto.key_for()
        made.append(proto)
        return proto

    yield build
    for p in made:
        for c in p.opened:
            with contextlib.suppress(Exception):
                c.close()
        p.servers.stop()


@pytest.fixture()
def proto(make_proto) -> Proto:
    return make_proto()


def client_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
