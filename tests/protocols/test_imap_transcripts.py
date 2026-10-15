"""Recorded byte-level IMAP transcripts for the tricky parts of the grammar: literals
(synchronizing and LITERAL+), quoting, framing limits, STARTTLS injection, timeouts."""

from __future__ import annotations

import ssl

import pytest

from mailsim.protocols import imap as imap_mod
from tests.protocols.conftest import AGENT, client_tls

pytestmark = pytest.mark.db


def login(c, key):
    assert c.cmd(f"a0 LOGIN {AGENT} {key}".encode())[-1].startswith(b"a0 OK")


def test_synchronizing_literals(proto):
    c = proto.raw()
    user, key = AGENT.encode(), proto.key.encode()
    c.send(b"a1 LOGIN {%d}\r\n" % len(user))
    assert c.line() == b"+ Ready for literal data\r\n"
    c.send(user + b" {%d}\r\n" % len(key))
    assert c.line() == b"+ Ready for literal data\r\n"
    c.send(key + b"\r\n")
    assert c.until(b"a1")[-1].startswith(b"a1 OK [CAPABILITY IMAP4rev1")


def test_non_synchronizing_literals_and_quoting(proto):
    c = proto.raw()
    key = proto.key.encode()
    lines = c.cmd(b'a1 LOGIN "finance@example.test" {%d+}\r\n%s' % (len(key), key))
    assert lines[-1].startswith(b"a1 OK")
    raw = b"From: x@example.test\r\nSubject: lit\r\n\r\nbody\r\n"
    lines = c.cmd(b"a2 APPEND INBOX (\\Seen) {%d+}\r\n%s" % (len(raw), raw))
    assert lines[-1].startswith(b"a2 OK [APPENDUID ")
    c.cmd(b"a3 SELECT {5+}\r\nINBOX")
    lines = c.cmd(b'a4 FETCH 3 (BODY.PEEK[HEADER.FIELDS ("Subject")])')
    assert lines[0] == b"* 3 FETCH (BODY[HEADER.FIELDS (SUBJECT)] {16}\r\nSubject: lit\r\n\r\n)\r\n"
    lines = c.cmd(b'a5 SEARCH SUBJECT "l\\"i\\\\t"')
    assert lines == [b"* SEARCH\r\n", b"a5 OK SEARCH completed\r\n"]
    lines = c.cmd(b"a6 SEARCH SUBJECT {3+}\r\nlit")
    assert lines[0] == b"* SEARCH 3\r\n"


def test_responses_quote_and_literal_strings(proto):
    raw = ('From: "Quote \\" Name" <q@example.test>\r\nTo: finance@example.test\r\n'
           "Subject: café \"quoted\" \\ slash\r\n\r\nx\r\n").encode()  # fmt: skip
    proto.mailbox.insert_message(raw, folder="inbox")
    c = proto.raw()
    login(c, proto.key)
    c.cmd(b"s SELECT INBOX")
    env = c.cmd(b"f FETCH 3 ENVELOPE")[0]
    assert b'{22}\r\ncaf\xc3\xa9 "quoted" \\ slash' in env  # 8-bit header text goes as a literal
    assert b'(("Quote \\" Name" NIL "q" "example.test"))' in env


@pytest.mark.parametrize(
    ("command", "reply"),
    [
        (b'a1 LOGIN "unterminated', b"a1 BAD unterminated quoted string"),
        (b'a1 LOGIN "bad\\escape" x', b"a1 BAD invalid escape in quoted string"),
        (b"a1 LOGIN (a b", b"a1 BAD unterminated list"),
        (b"a1 LOGIN a)", b"a1 BAD unexpected ')'"),
        (b"a1 LOGIN a  b", b"a1 BAD unexpected character ' '"),
        (b"a1 LOGIN (a )", b"a1 BAD trailing SP in list"),
        (b"a1 LOGIN a(b", b"a1 BAD expected SP between arguments"),
        (b"a1 LOGIN a]", b"a1 BAD usage: LOGIN user password"),  # ']' is an ASTRING-CHAR
        (b"a1 LOGIN a[b", b"a1 BAD usage: LOGIN user password"),
        (b"a1 FETCH 1 BODY[x", b"a1 BAD unterminated '['"),
        (b"a1 NOOP " + b"(" * 40, b"a1 BAD lists nested too deeply"),
        (b"a1 LOGIN {x}", b"a1 BAD malformed literal"),
        (b"a1 LOGIN a", b"a1 BAD usage: LOGIN user password"),
        (b"a1 LOGIN a ()", b"a1 BAD expected a string"),
        (b"a1 LOGIN", b"a1 BAD usage: LOGIN user password"),
        (b"a1", b"a1 BAD missing command"),
        (b'a1 "LOGIN"', b"a1 BAD missing command"),
        (b"+tag NOOP", b"* BAD missing command"),
        (b'"q" NOOP', b"* BAD missing command"),
        (b'a1 LOGIN "x', b"a1 BAD unterminated quoted string"),
        (b"(x NOOP", b"* BAD unterminated list"),
    ],
)
def test_syntax_errors(proto, command, reply):
    c = proto.raw()
    c.send(command + b"\r\n")
    assert c.line() == reply + b"\r\n"
    assert c.cmd(b"z NOOP") == [b"z OK NOOP completed\r\n"]  # the session survives


def test_framing_limits(proto, monkeypatch):
    c = proto.raw()
    c.send(b"\r\n")  # empty lines are ignored
    assert c.cmd(b"z NOOP") == [b"z OK NOOP completed\r\n"]
    c.send(b"a1 LOGIN {999999999}\r\n")  # synchronizing literal too large: refused, no "+"
    assert c.line() == b"a1 NO [TOOBIG] literal too large\r\n"
    c.send(b"(bad {999999999}\r\n")
    assert c.line() == b"* NO [TOOBIG] literal too large\r\n"
    c.send(b"a2 LOGIN {999999999+}\r\n")  # non-synchronizing: the client already sends it
    assert c.line() == b"* BYE literal too large\r\n"
    assert c.line() == b""
    d = proto.raw()
    d.send(b"a3 NOOP " + b"x" * (imap_mod.LINE_LIMIT + 10) + b"\r\n")
    assert d.line() == b"* BYE command line too long\r\n"


def test_logout_and_disconnects(proto):
    c = proto.raw()
    assert c.cmd(b"a LOGOUT") == [b"* BYE mailsim logging out\r\n", b"a OK LOGOUT completed\r\n"]
    assert c.line() == b""
    d = proto.raw()
    d.send(b"a1 LOGIN {3}\r\n")
    d.line()
    d.close()  # client vanishes mid-literal: the server just drops the connection


def test_idle_timeout(make_proto):
    p = make_proto(idle_timeout=0.3)
    c = p.raw()
    assert c.line() == b"* BYE autologout; idle for too long\r\n"


def test_starttls_rejects_pipelined_plaintext(make_proto):
    p = make_proto(tls=True)
    c = p.raw()
    c.send(b"a STARTTLS\r\nb LOGIN injected x\r\n")
    assert c.line() == b"a OK Begin TLS negotiation now\r\n"
    assert c.line() == b"* BYE data received after STARTTLS before the TLS handshake\r\n"


def test_starttls_then_capabilities(make_proto):
    p = make_proto(tls=True)
    c = p.raw()
    assert b"STARTTLS" in c.greeting
    assert c.cmd(b"a STARTTLS") == [b"a OK Begin TLS negotiation now\r\n"]
    c.sock = client_tls().wrap_socket(c.sock)
    assert isinstance(c.sock, ssl.SSLSocket)
    caps = c.cmd(b"b CAPABILITY")[0]
    assert b"STARTTLS" not in caps and b"AUTH=PLAIN" in caps
    assert c.cmd(b"c STARTTLS")[-1] == b"c BAD STARTTLS not available\r\n"
    login(c, p.key)
    assert c.cmd(b"d SELECT INBOX")[-1] == b"d OK [READ-WRITE] SELECT completed\r\n"


def test_internal_errors_are_contained(proto, monkeypatch):
    c = proto.raw()
    login(c, proto.key)

    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(imap_mod.ImapSession, "_folders", boom)
    assert c.cmd(b'a LIST "" *') == [b"a NO [SERVERBUG] internal error\r\n"]
