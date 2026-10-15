"""Review follow-ups: DoS bounds, error containment, revocation, change-feed caching, \\Seen."""

from __future__ import annotations

import asyncio
import base64
import imaplib
import smtplib
import threading
import time

import pytest

from mailsim.api.auth import revoke_agent_keys
from mailsim.protocols.server import ProtocolServers, is_loopback
from mailsim.protocols.smtp import SandboxHandler
from tests.protocols.conftest import AGENT, VENDOR
from tests.protocols.test_imap import fetch_map, ok
from tests.protocols.test_smtp import message

pytestmark = pytest.mark.db


def agent_reads(p) -> int:
    return sum(
        1 for a in p.ctl.actions(p.run) if a["actor"] == "agent" and a["operation"] == "read"
    )


def inject(p, subject):
    p.ctl.inject(p.run, p.mailbox_id, {"from": VENDOR, "to": [AGENT], "subject": subject,
                                       "text": "t"})  # fmt: skip


# ------------------------------------------------------------------------ IMAP bounds
def test_list_wildcards_are_linear(proto):
    c = proto.imap()
    started = time.monotonic()
    assert ok(c.list('""', "*" * 40 + "Z")) == [None]
    assert ok(c.list('""', "%" * 200 + "Q")) == [None]
    assert time.monotonic() - started < 2
    with pytest.raises(imaplib.IMAP4.error, match="pattern too long"):
        c.list('""', "*" * 300)


def test_search_depth_and_missing_keys(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    with pytest.raises(imaplib.IMAP4.error, match="nested too deeply"):
        c.search(None, *(["NOT"] * 40), "ALL")
    with pytest.raises(imaplib.IMAP4.error, match="missing search key"):
        c.search(None, "OR", "SEEN")
    with pytest.raises(imaplib.IMAP4.error, match="invalid sequence set"):
        c.fetch("12345678901", "FLAGS")


def test_preauth_literals_are_small(proto):
    c = proto.raw()
    c.send(b"a LOGIN {9000}\r\n")
    assert c.line() == b"a NO [TOOBIG] literal too large\r\n"


def test_connection_caps(make_proto):
    p = make_proto(max_connections=1)
    first = p.raw()
    assert first.greeting.startswith(b"* OK")
    second = p.raw()
    assert second.greeting == b"* BYE too many connections, try again later\r\n"
    s1 = p.smtp(login=False)
    with pytest.raises(smtplib.SMTPConnectError) as ei:
        smtplib.SMTP("127.0.0.1", p.servers.smtp_port, timeout=5)
    assert ei.value.smtp_code == 421
    s1.quit()


def test_non_loopback_requires_tls(env):
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("mail.example.test")
    with pytest.raises(ValueError, match="without TLS"):
        ProtocolServers(env.engine, host="0.0.0.0")
    ProtocolServers(env.engine, host="0.0.0.0", allow_insecure_remote=True)  # not started


# ------------------------------------------------------------------------ IMAP semantics
def test_idle_noop_is_one_read(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    before = agent_reads(proto)
    ok(c.noop())
    assert agent_reads(proto) - before == 1
    ok(c.fetch("1:*", "(FLAGS UID)"))  # the cached listing serves FETCH too
    assert agent_reads(proto) - before == 2
    inject(proto, "fresh")
    ok(c.noop())
    assert c.untagged_responses["EXISTS"][-1] == b"3"


def test_body_fetch_sets_seen(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    assert fetch_map(ok(c.fetch("1", "(RFC822.HEADER)")))[1].startswith(b"1 (RFC822.HEADER")
    got = ok(c.fetch("1", "(BODY[TEXT])"))
    assert got[0][0].endswith(b"}") and b"FLAGS (\\Seen)" in got[1]
    assert "SEEN" in proto.mailbox.list_messages(folder="inbox").items[0].flags
    assert fetch_map(ok(c.fetch("1", "(FLAGS RFC822)")))  # already seen: no second mark
    ok(c.noop())
    assert "FETCH" not in c.untagged_responses  # no duplicate unsolicited flag update
    writes = [a for a in proto.actions() if a[0] == "agent" and a[1] != "read"]
    assert writes == []  # recorded as a read, never budgeted
    d = proto.imap()
    ok(d.select("INBOX", readonly=True))
    ok(d.fetch("2", "(BODY[])"))
    assert "SEEN" not in proto.mailbox.list_messages(folder="inbox").items[1].flags


def test_non_uid_move_after_concurrent_expunge(proto):
    inject(proto, "third")
    a, b = proto.imap(), proto.imap()
    ok(a.select("INBOX"))
    ok(b.select("INBOX"))
    ok(b.store("1", "+FLAGS", "\\Deleted"))
    ok(b.expunge())
    typ, _ = a._simple_command("MOVE", "2", "Archive")  # 2 = the PO in a's (old) numbering
    assert typ == "OK"
    archived = proto.mailbox.list_messages(folder="archive").items
    assert [m.subject for m in archived] == ["Re: Invoice INV-001"]


def test_uid_store_echo_uses_current_numbering(proto):
    a, b = proto.imap(), proto.imap()
    ok(a.select("INBOX"))
    ok(b.select("INBOX"))
    ok(b.store("1", "+FLAGS", "\\Deleted"))
    ok(b.expunge())
    assert ok(a.uid("STORE", "2", "+FLAGS", "\\Flagged")) == [b"1 (UID 2 FLAGS (\\Flagged))"]
    assert a.untagged_responses["EXPUNGE"] == [b"1"]


def test_keywords_ignore_case(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    ok(c.store("1", "+FLAGS", "(urgent)"))
    assert ok(c.store("1", "+FLAGS", "(URGENT)")) == [b"1 (FLAGS (urgent))"]
    assert ok(c.search(None, "KEYWORD", "Urgent")) == [b"1"]
    assert ok(c.store("1", "-FLAGS", "(Urgent)")) == [b"1 (FLAGS ())"]


def test_revoked_keys_end_sessions(make_proto):
    p = make_proto(reverify_seconds=0)
    c = p.imap()
    ok(c.select("INBOX"))
    s = p.smtp()
    with p.env.session_factory.begin() as session:
        revoke_agent_keys(session, p.env.scope.tenant_id, p.env.scope.project_id, p.run)
    with pytest.raises(imaplib.IMAP4.abort, match="revoked"):
        c.noop()
    code, text = s.mail(AGENT)
    assert code == 421 and b"revoked" in text


@pytest.mark.parametrize("stage", ["rcpt", "data"])
def test_revocation_mid_transaction(make_proto, stage):
    p = make_proto(reverify_seconds=0)
    s = p.smtp()
    s.mail(AGENT)
    if stage == "data":
        s.rcpt(VENDOR)

    with p.env.session_factory.begin() as session:
        revoke_agent_keys(session, p.env.scope.tenant_id, p.env.scope.project_id, p.run)
    code, _ = s.rcpt(VENDOR) if stage == "rcpt" else s.data(message())
    assert code == 421


def test_reset_while_not_selected_refreshes_folders(proto):
    c = proto.imap()
    proto.ctl.reset_run(proto.run)
    proto.ctl.start_run(proto.run)
    assert len(ok(c.list())) == 5
    ok(c.select("INBOX"))
    got = ok(c.fetch("2", "(FLAGS BODY[1])"))
    assert b"FLAGS (\\Seen)" in got[0][0] + got[-1]


# ------------------------------------------------------------------------ SMTP robustness
def test_smtp_internal_errors_are_not_leaked(proto, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret db detail\r\n250 injected")

    s = proto.smtp()
    s.mail(AGENT)
    monkeypatch.setattr(proto.servers.authenticator, "recipient_policy", boom)
    assert s.rcpt(VENDOR) == (451, b"4.3.0 internal error")
    monkeypatch.undo()
    s.rset()
    s.mail(AGENT)
    s.rcpt(VENDOR)
    monkeypatch.setattr(SandboxHandler, "_submit", boom)
    assert s.data(message()) == (451, b"4.3.0 internal error")
    t = proto.smtp(login=False)
    monkeypatch.setattr(proto.servers.authenticator, "login", boom)
    code, text = t.docmd("AUTH", "PLAIN " + base64.b64encode(b"\0a\0b").decode())
    assert (code, text) == (451, b"4.3.0 internal error")


def test_smtp_auth_runs_off_the_event_loop(proto, monkeypatch):
    imap = proto.imap()
    real = proto.servers.authenticator.login

    def slow(user, password):
        time.sleep(1.5)
        return real(user, password)

    monkeypatch.setattr(proto.servers.authenticator, "login", slow)
    s = proto.smtp(login=False)
    worker = threading.Thread(target=s.login, args=(AGENT, proto.key))
    worker.start()
    time.sleep(0.2)
    started = time.monotonic()
    ok(imap.noop())
    assert time.monotonic() - started < 1.0
    worker.join()
    assert s.mail(AGENT)[0] == 250


def test_smtp_auth_attempts_are_limited(proto):
    s = proto.smtp(login=False)
    bad = "PLAIN " + base64.b64encode(f"\0{AGENT}\0nope".encode()).decode()
    for _ in range(3):
        assert s.docmd("AUTH", bad)[0] == 535
    assert s.docmd("AUTH", bad)[0] == 421


@pytest.mark.parametrize(
    ("args", "reply"),
    [
        ("PLAIN !!!", (501, b"5.5.2 Can't decode base64")),
        ("PLAIN " + base64.b64encode(b"only-one-part").decode(), (501, b"5.5.2 Can't split auth value")),
        ("LOGIN !!!", (501, b"5.5.2 Can't decode base64")),
    ],
)  # fmt: skip
def test_smtp_auth_syntax(proto, args, reply):
    assert proto.smtp(login=False).docmd("AUTH", args) == reply


def test_smtp_auth_challenges(proto):
    s = proto.smtp(login=False)
    assert s.docmd("AUTH", "PLAIN")[0] == 334
    good = base64.b64encode(f"\0{AGENT}\0{proto.key}".encode()).decode()
    assert s.docmd(good)[0] == 235
    s = proto.smtp(login=False)
    assert s.docmd("AUTH", "PLAIN")[0] == 334
    assert s.docmd("*") == (501, b"5.7.0 Auth aborted")
    assert s.docmd("AUTH", "LOGIN")[0] == 334
    assert s.docmd("*") == (501, b"5.7.0 Auth aborted")
    user = base64.b64encode(AGENT.encode()).decode()
    assert s.docmd("AUTH", f"LOGIN {user}")[0] == 334
    assert s.docmd("*") == (501, b"5.7.0 Auth aborted")


# ------------------------------------------------------------------------ re-review follow-ups
def test_starttls_sessions_do_not_leak_connection_slots(make_proto):
    from tests.protocols.conftest import client_tls

    p = make_proto(tls=True, max_connections=2)
    for _ in range(4):
        s = smtplib.SMTP("127.0.0.1", p.servers.smtp_port, timeout=5)
        s.ehlo()
        s.starttls(context=client_tls())
        s.ehlo()
        s.quit()
    deadline = time.monotonic() + 5
    while p.servers._smtp_sessions and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not p.servers._smtp_sessions
    s = smtplib.SMTP("127.0.0.1", p.servers.smtp_port, timeout=5)
    assert s.noop()[0] == 250
    s.quit()


def test_preauth_command_size_is_capped(proto):
    c = proto.raw()
    c.send(b"a LOGIN {8000}\r\n")
    assert c.line() == b"+ Ready for literal data\r\n"
    c.send(b"x" * 8000 + b" {8000}\r\n")  # each literal is within the 8 KB limit
    assert c.line() == b"+ Ready for literal data\r\n"
    c.send(b"y" * 8000 + b" {1000}\r\n")  # ... but the command would pass 16 KB
    assert c.line() == b"a NO [TOOBIG] literal too large\r\n"
    d = proto.raw()
    d.send(b"a LOGIN {8000}\r\n")
    d.line()
    d.send(b"x" * 8000 + b" " + b"y" * 9000 + b"\r\n")
    assert d.line() == b"* BYE command too large\r\n"


def test_non_reading_clients_are_dropped(proto, monkeypatch):
    import socket

    from mailsim.protocols import imap as imap_mod

    monkeypatch.setattr(imap_mod, "WRITE_TIMEOUT", 0.5)
    big = b"From: a@example.test\r\nSubject: big\r\n\r\n" + (b"z" * 998 + b"\r\n") * 2000
    for _ in range(4):
        proto.mailbox.insert_message(big, folder="inbox")
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect(("127.0.0.1", proto.servers.imap_port))
    sock.sendall(f"a LOGIN {AGENT} {proto.key}\r\nb SELECT INBOX\r\n".encode())
    sock.sendall(b"c FETCH 1:* (BODY.PEEK[])\r\nd FETCH 1:* (BODY.PEEK[])\r\n")  # never read
    other = proto.imap()
    deadline = time.monotonic() + 20
    while len(proto.servers._clients) > 1 and time.monotonic() < deadline:
        ok(other.noop())  # the other session keeps being served meanwhile
        time.sleep(0.1)
    assert len(proto.servers._clients) == 1
    sock.close()


def test_credential_recheck_failures_close_sessions(proto, monkeypatch):
    def boom(agent):
        raise RuntimeError("db down")

    c = proto.imap()
    s = proto.smtp()
    monkeypatch.setattr(proto.servers.authenticator, "still_valid", boom)
    with pytest.raises(imaplib.IMAP4.abort, match="revoked"):
        c.noop()
    assert s.mail(AGENT)[0] == 421


def test_smtp_plain_rejects_foreign_authzid(proto):
    s = proto.smtp(login=False)
    blob = base64.b64encode(f"{VENDOR}\0{AGENT}\0{proto.key}".encode()).decode()
    assert s.docmd("AUTH", f"PLAIN {blob}")[0] == 535
    same = base64.b64encode(f"{AGENT}\0{AGENT}\0{proto.key}".encode()).decode()
    assert s.docmd("AUTH", f"PLAIN {same}")[0] == 235


def test_store_echo_skips_messages_removed_meanwhile(proto, monkeypatch):
    from mailsim.engine.facades import AgentMailbox

    real = AgentMailbox.set_flags

    def flag_then_vanish(self, message_id, **kw):
        real(self, message_id, **kw)
        self.delete(message_id)  # e.g. another adapter deleting it concurrently

    c = proto.imap()
    ok(c.select("INBOX"))
    monkeypatch.setattr(AgentMailbox, "set_flags", flag_then_vanish)
    assert ok(c.store("1", "+FLAGS", "\\Flagged")) == [None]


def test_connection_aborts_when_the_greeting_cannot_be_sent(monkeypatch):
    from mailsim.protocols import imap as imap_mod

    monkeypatch.setattr(imap_mod, "WRITE_TIMEOUT", 0.05)

    class StuckWriter:
        def __init__(self):
            self.aborted = 0
            self.transport = self

        def write(self, data):
            pass

        async def drain(self):
            await asyncio.sleep(10)

        def abort(self):
            self.aborted += 1

        def close(self):
            pass

        async def wait_closed(self):
            return None

    async def main():
        writer = StuckWriter()
        session = imap_mod.ImapSession(None)  # type: ignore[arg-type]
        await imap_mod.ImapConnection(session, asyncio.StreamReader(), writer).run()  # type: ignore[arg-type]
        return writer.aborted

    assert asyncio.run(main()) == 2  # once for the timeout, once in the final cleanup
