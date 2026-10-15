"""Listener lifecycle: background thread, asyncio embedding, `run()`, TLS configuration."""

from __future__ import annotations

import asyncio
import imaplib
import socket

import pytest

from mailsim.protocols import server as server_mod
from mailsim.protocols.server import ProtocolServers, run
from mailsim.protocols.tls import self_signed_pem, server_context
from tests.protocols.conftest import AGENT, client_tls

pytestmark = pytest.mark.db


def test_context_manager_and_restart(env):
    with ProtocolServers(env.engine, imap_port=0, smtp_port=0) as servers:
        assert servers.imap_port and servers.smtp_port and servers.imap_port != servers.smtp_port
        c = imaplib.IMAP4("127.0.0.1", servers.imap_port, timeout=5)
        assert c.welcome.startswith(b"* OK")
        # the client is still connected when the listener stops: the connection is closed
    with pytest.raises(imaplib.IMAP4.abort):
        c.noop()
    servers.stop()  # idempotent


def test_port_in_use_is_reported(env):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    try:
        with pytest.raises(OSError):
            ProtocolServers(env.engine, imap_port=sock.getsockname()[1], smtp_port=0).start()
    finally:
        sock.close()


def test_operator_supplied_certificate(env, tmp_path):
    cert, key = self_signed_pem("mail.test")
    (tmp_path / "c.pem").write_bytes(cert)
    (tmp_path / "k.pem").write_bytes(key)
    ctx = server_context(tmp_path / "c.pem", tmp_path / "k.pem")
    with ProtocolServers(env.engine, imap_port=0, smtp_port=0, tls_context=ctx) as servers:
        c = imaplib.IMAP4("127.0.0.1", servers.imap_port, timeout=5)
        c.starttls(ssl_context=client_tls())
        assert c.sock.getpeercert(binary_form=True)
        c.logout()


def test_serve_forever_on_a_caller_loop(make_proto):
    p = make_proto()
    p.servers.stop()

    async def main() -> bool:
        servers = ProtocolServers(p.env.engine, imap_port=0, smtp_port=0)
        task = asyncio.create_task(servers.serve_forever())
        while not servers._servers:
            await asyncio.sleep(0.01)
        reader, writer = await asyncio.open_connection("127.0.0.1", servers.imap_port)
        greeting = await reader.readline()
        writer.write(f"a LOGIN {AGENT} {p.key}\r\n".encode())
        await writer.drain()
        reply = await reader.readline()
        writer.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return greeting.startswith(b"* OK") and reply.startswith(b"a OK")

    assert asyncio.run(main())


def test_run_entry_point(env, monkeypatch):
    seen = {}

    def fake_run(coro):
        seen["coro"] = coro.__qualname__
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(server_mod.asyncio, "run", fake_run)
    run(env.engine, imap_port=0, smtp_port=0, tls=True)  # returns quietly on Ctrl-C
    assert seen["coro"] == "ProtocolServers.serve_forever"
