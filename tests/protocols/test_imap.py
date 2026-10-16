"""IMAP4rev1 listener driven with the stdlib `imaplib` client over loopback."""

from __future__ import annotations

import imaplib
import re

import pytest

from tests.protocols.conftest import AGENT, client_tls, open_agent

pytestmark = pytest.mark.db

RAW = (
    b"From: Someone <someone@example.test>\r\nTo: finance@example.test\r\n"
    b"Subject: appended\r\nMessage-ID: <appended-1@example.test>\r\n\r\nappended body\r\n"
)


def ok(result):
    typ, data = result
    assert typ == "OK", data
    return data


def fetch_map(data):
    """imaplib fetch data -> {seq: response text with literals inlined}."""
    out, cur = {}, b""
    for item in data:
        if isinstance(item, tuple):
            cur += item[0] + item[1]
        elif item == b")":
            cur += item
        else:
            cur += item
        m = re.match(rb"^(\d+) \(", cur)
        if m and cur.endswith(b")"):
            out[int(m.group(1))] = cur
            cur = b""
    return out


# ------------------------------------------------------------------------ authentication
def test_greeting_and_capabilities(proto):
    c = proto.imap(login=False)
    assert c.welcome.startswith(b"* OK [CAPABILITY IMAP4rev1")
    caps = set(c.capabilities)
    assert {"IMAP4REV1", "AUTH=PLAIN", "SASL-IR", "UIDPLUS", "MOVE", "LITERAL+"} <= caps
    assert "STARTTLS" not in caps and "IDLE" not in caps
    c.login(AGENT, proto.key)
    typ, data = c.capability()
    assert typ == "OK" and b"AUTH=PLAIN" not in data[0] and b"OBJECTID" in data[0]
    assert ok(c.noop())
    assert c.logout()[0] == "BYE"


@pytest.mark.parametrize("case", ["bad-key", "wrong-user", "controller-key", "malformed"])
def test_login_failures(proto, case):
    c = proto.imap(login=False)
    user, key = AGENT, proto.key
    if case == "bad-key":
        key = proto.key[:-4] + "AAAA"
    elif case == "wrong-user":
        user = "vendor@example.test"
    elif case == "controller-key":
        with proto.env.session_factory.begin() as s:
            from mailsim.api.auth import issue_key

            key = issue_key(s, tenant_id=proto.env.scope.tenant_id,
                            project_id=proto.env.scope.project_id, role="controller").api_key  # fmt: skip
    else:
        key = "not-a-key"
    with pytest.raises(imaplib.IMAP4.error, match="AUTHENTICATIONFAILED"):
        c.login(user, key)


def test_login_to_another_mailbox_is_denied(proto):
    """A key bound to a mailbox that is not the agent's never gets a session."""
    c = proto.imap(login=False)
    with pytest.raises(imaplib.IMAP4.error, match="AUTHORIZATIONFAILED.*PERMISSION_DENIED"):
        c.login("vendor@example.test", proto.key_for("vendor"))
    assert ("agent", "read", "PERMISSION_DENIED") in proto.actions()


def test_login_before_the_run_starts_is_unavailable(make_proto):
    p = make_proto(start=False)
    c = p.imap(login=False)
    with pytest.raises(imaplib.IMAP4.error, match="UNAVAILABLE"):
        c.login(AGENT, p.key)


def test_three_failures_disconnect(proto):
    c = proto.raw()
    for i in range(2):
        assert b"NO [AUTHENTICATIONFAILED]" in c.cmd(f"a{i} LOGIN {AGENT} nope".encode())[-1]
    lines = c.cmd(f"a3 LOGIN {AGENT} nope".encode())
    assert b"NO [AUTHENTICATIONFAILED]" in lines[-1]
    assert c.line() == b"* BYE too many failed attempts\r\n"
    assert c.line() == b""


def test_authenticate_plain(proto):
    c = proto.imap(login=False)
    typ, _ = c.authenticate("PLAIN", lambda _: f"\0{AGENT}\0{proto.key}".encode())
    assert typ == "OK"
    assert ok(c.select("INBOX")) == [b"2"]


def test_authenticate_variants(proto):
    import base64

    good = base64.b64encode(f"{AGENT}\0{AGENT}\0{proto.key}".encode()).decode()
    mismatch = base64.b64encode(f"other\0{AGENT}\0{proto.key}".encode()).decode()
    c = proto.raw()
    assert b"NO [CANNOT]" in c.cmd(b"a1 AUTHENTICATE CRAM-MD5")[-1]
    assert b"BAD" in c.cmd(b"a2 AUTHENTICATE")[-1]
    c.send(b"a3 AUTHENTICATE PLAIN\r\n")
    assert c.line() == b"+ \r\n"
    c.send(b"*\r\n")
    assert c.line() == b"a3 BAD AUTHENTICATE cancelled\r\n"
    assert b"a4 BAD malformed" in c.cmd(b"a4 AUTHENTICATE PLAIN !!!!")[-1]
    assert b"AUTHORIZATIONFAILED" in c.cmd(f"a5 AUTHENTICATE PLAIN {mismatch}".encode())[-1]
    assert c.cmd(b"a6 AUTHENTICATE PLAIN =")[-1].startswith(b"a6 BAD")
    assert c.cmd(f"a7 AUTHENTICATE PLAIN {good}".encode())[-1].startswith(b"a7 OK")
    assert c.cmd(b"a8 LOGIN x y")[-1] == b"a8 BAD already authenticated\r\n"


def test_starttls_not_offered_without_tls(proto):
    c = proto.raw()
    assert c.cmd(b"t1 STARTTLS")[-1] == b"t1 BAD STARTTLS not available\r\n"


def test_starttls(make_proto):
    p = make_proto(tls=True)
    c = p.imap(login=False)
    assert "STARTTLS" in c.capabilities
    ok(c.starttls(ssl_context=client_tls()))
    assert "STARTTLS" not in c.capabilities  # imaplib re-reads CAPABILITY after the handshake
    c.login(AGENT, p.key)
    assert ok(c.select("INBOX")) == [b"2"]


# ------------------------------------------------------------------------ folders
def test_list_lsub_status_namespace_id(proto):
    c = proto.imap()
    names = [line.decode() for line in ok(c.list())]
    assert names == [
        '(\\HasNoChildren) "/" "INBOX"',
        '(\\HasNoChildren \\Sent) "/" "Sent"',
        '(\\HasNoChildren \\Drafts) "/" "Drafts"',
        '(\\HasNoChildren \\Trash) "/" "Trash"',
        '(\\HasNoChildren \\Archive) "/" "Archive"',
    ]
    assert ok(c.list('""', "inbox")) == [b'(\\HasNoChildren) "/" "INBOX"']
    assert [x.split(b" ")[-1] for x in ok(c.list('""', "%r%"))] == [b'"Drafts"', b'"Trash"',
                                                                      b'"Archive"']  # fmt: skip
    assert ok(c.list('""', '""')) == [b'(\\Noselect) "/" ""']
    assert ok(c.list('""', "nothing*")) == [None]
    assert len(ok(c.lsub())) == 5
    assert ok(c.lsub('""', '""')) == [None]
    status = ok(c.status("INBOX", "(MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)"))[0]
    assert re.fullmatch(rb'"INBOX" \(MESSAGES 2 RECENT 0 UIDNEXT 3 UIDVALIDITY \d+ UNSEEN 2\)',
                        status)  # fmt: skip
    assert c.status("Nope", "(MESSAGES)")[0] == "NO"
    with pytest.raises(imaplib.IMAP4.error, match="unknown STATUS item"):
        c.status("INBOX", "(BOGUS)")
    assert ok(c.namespace()) == [b'(("" "/")) NIL NIL']
    assert ok(c.subscribe("Sent"))
    assert c.subscribe("Nope")[0] == "NO"
    assert ok(c.unsubscribe("Sent"))
    for result in (c.create("Projects"), c.delete("Sent"), c.rename("Sent", "Out")):
        assert result[0] == "NO" and b"UNSUPPORTED_OPERATION" in result[1][0]


def test_select_and_examine(proto):
    c = proto.imap()
    assert ok(c.select("INBOX")) == [b"2"]
    resp = {k: v for k, v in c.untagged_responses.items()}
    assert resp["FLAGS"] == [b"(\\Answered \\Flagged \\Deleted \\Seen \\Draft)"]
    assert resp["UNSEEN"] == [b"1"] and resp["UIDNEXT"] == [b"3"] and resp["RECENT"] == [b"0"]
    assert b"\\*" in resp["PERMANENTFLAGS"][0]
    assert resp["READ-WRITE"] == [b""]
    assert c.select("Nope")[0] == "NO"
    assert c.state == "AUTH"  # a failed SELECT deselects
    ok(c.select("INBOX", readonly=True))
    assert c.untagged_responses["PERMANENTFLAGS"] == [b"()"]
    assert c.store("1", "+FLAGS", "\\Seen")[0] == "NO"
    assert c.expunge()[0] == "NO"
    assert c.uid("MOVE", "1", "Archive")[0] == "NO"
    ok(c.uid("COPY", "1", "Archive"))  # copying out of a read-only mailbox is fine
    ok(c.close())  # read-only CLOSE expunges nothing
    assert ok(c.select("Archive")) == [b"1"]


def test_wrong_state_and_unknown_commands(proto):
    c = proto.raw()
    assert c.cmd(b"a1 SELECT INBOX")[-1] == b"a1 BAD authenticate first\r\n"
    assert c.cmd(b"a2 FETCH 1 FLAGS")[-1] == b"a2 BAD no mailbox selected\r\n"
    assert c.cmd(b"a3 FROBNICATE")[-1] == b"a3 BAD unknown command FROBNICATE\r\n"
    c.cmd(f"a4 LOGIN {AGENT} {proto.key}".encode())
    assert c.cmd(b"a5 CHECK")[-1] == b"a5 BAD no mailbox selected\r\n"
    assert c.cmd(b"a6 CAPABILITY extra")[-1].startswith(b"a6 BAD usage")
    assert c.cmd(b'a7 ID ("name" "imaplib")')[0] == b'* ID ("name" "IMAP" "vendor" "IMAP")\r\n'


# ------------------------------------------------------------------------ FETCH
def test_fetch_attributes(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    data = fetch_map(ok(c.fetch("1:*", "(UID FLAGS INTERNALDATE RFC822.SIZE ENVELOPE "
                                       "BODYSTRUCTURE EMAILID THREADID)")))  # fmt: skip
    first = data[1].decode()
    assert first.startswith('1 (UID 1 FLAGS () INTERNALDATE "15-Oct-2026 08:30:00 +0000"')
    assert '"Invoice INV-001" (("Example Vendor" NIL "vendor" "example.test"))' in first
    assert (
        'BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 65 1 NIL NIL NIL '
        'NIL)("APPLICATION" "PDF" ("NAME" "INV-001.pdf") NIL NIL "BASE64" 60 NIL ("ATTACHMENT"'
        ' ("FILENAME" "INV-001.pdf")) NIL NIL) "MIXED" ("BOUNDARY" "B1") NIL NIL NIL)'
    ) in first
    assert re.search(r"EMAILID \(msg_[0-9a-f]+\) THREADID \(thr_[0-9a-f]+\)", first)
    second = data[2].decode()
    assert '"<invoice-001@example.test>" "<po-001@example.test>")' in second
    for macro in ("ALL", "FAST", "FULL"):
        assert b"RFC822.SIZE" in fetch_map(ok(c.fetch("1", macro)))[1]
    assert b"BODY (" in fetch_map(ok(c.fetch("1", "FULL")))[1]
    assert b"BODY (" in fetch_map(ok(c.fetch("2", "BODY")))[2]


def test_fetch_body_sections_and_partials(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    whole = ok(c.fetch("1", "(BODY.PEEK[])"))[0][1]
    assert whole.startswith(b"Message-ID: <invoice-001@example.test>") and b"--B1--" in whole
    assert ok(c.fetch("1", "(RFC822)"))[0][1] == whole
    header = ok(c.fetch("1", "(BODY.PEEK[HEADER])"))[0][1]
    assert header.endswith(b"\r\n\r\n") and b"Subject: Invoice INV-001" in header
    assert ok(c.fetch("1", "(RFC822.HEADER)"))[0][1] == header
    text = ok(c.fetch("1", "(BODY.PEEK[TEXT])"))[0][1]
    assert whole == header + text and ok(c.fetch("1", "(RFC822.TEXT)"))[0][1] == text
    assert ok(c.fetch("1", "(BODY.PEEK[1])"))[0][1] == (
        b"Please confirm the payment date for invoice INV-001 (INR 250000)."
    )
    assert ok(c.fetch("1", "(BODY.PEEK[2])"))[0][1].startswith(b"JVBERi0x")
    assert b"application/pdf" in ok(c.fetch("1", "(BODY.PEEK[2.MIME])"))[0][1]
    fields = ok(c.fetch("1", "(BODY.PEEK[HEADER.FIELDS (subject FROM)])"))[0]
    assert fields[0].endswith(b"BODY[HEADER.FIELDS (SUBJECT FROM)] {72}")
    assert fields[1] == (
        b"From: Example Vendor <vendor@example.test>\r\nSubject: Invoice INV-001\r\n\r\n"
    )
    rest = ok(c.fetch("1", "(BODY.PEEK[HEADER.FIELDS.NOT (Subject From Date To)])"))[0][1]
    assert b"Subject" not in rest and b"Message-ID" in rest
    part = ok(c.fetch("1", "(BODY.PEEK[]<0.10>)"))[0]
    assert part[0].endswith(b"BODY[]<0> {10}") and part[1] == whole[:10]
    tail = ok(c.fetch("1", "(BODY.PEEK[TEXT]<5>)"))[0]
    assert tail[1] == text[5:]
    assert ok(c.fetch("1", "(BODY.PEEK[]<100000.5>)"))[0][1] == b""
    assert (
        ok(c.fetch("2", "(BODY.PEEK[1])"))[0][1]
        == b"Purchase order PO-001 matches the invoice.\r\n"
    )
    assert fetch_map(ok(c.fetch("2", "FLAGS")))[2] == b"2 (FLAGS ())"  # PEEK never sets \Seen


@pytest.mark.parametrize(
    ("items", "error"),
    [
        ("(BODY[3])", "no such body part"),
        ("(BODY[1.HEADER])", "needs a message/rfc822 part"),
        ("(BODY[0])", "part numbers start at 1"),
        ("(BODY[MIME])", "MIME requires a part number"),
        ("(BODY[HEADER.FIELDS])", "requires a field list"),
        ("(BODY[HEADER.FIELDS ()])", "invalid header field list"),
        ("(BODY[FOO])", "invalid section"),
        ("(BOGUS)", "unknown FETCH attribute"),
        ("()", "empty FETCH attribute list"),
        ('"FLAGS"', "invalid FETCH attribute list"),
    ],
)
def test_fetch_errors(proto, items, error):
    c = proto.imap()
    ok(c.select("INBOX"))
    with pytest.raises(imaplib.IMAP4.error, match=re.escape(error)):
        c.fetch("1", items)


def test_fetch_sequence_errors_and_modifiers(proto):
    c = proto.raw()
    c.cmd(f"a LOGIN {AGENT} {proto.key}".encode())
    c.cmd(b"b SELECT INBOX")
    assert c.cmd(b"c FETCH 3 FLAGS")[-1] == b"c BAD invalid message sequence number\r\n"
    assert c.cmd(b"d FETCH 0 FLAGS")[-1].startswith(b"d BAD 0 is not a valid")
    assert c.cmd(b"e FETCH x FLAGS")[-1].startswith(b"e BAD invalid sequence set")
    assert b"UNSUPPORTED_OPERATION" in c.cmd(b"f FETCH 1 FLAGS (CHANGEDSINCE 1)")[-1]
    assert c.cmd(b"g FETCH 1")[-1].startswith(b"g BAD usage")
    assert c.cmd(b"h UID FETCH 99:* FLAGS")[0] == b"* 2 FETCH (UID 2 FLAGS ())\r\n"
    assert c.cmd(b"i UID FETCH 50 FLAGS") == [b"i OK UID FETCH completed\r\n"]
    assert c.cmd(b"j UID FROB 1")[-1].startswith(b"j BAD unknown UID command")
    assert c.cmd(b"k UID")[-1].startswith(b"k BAD usage")
    c.cmd(b"l SELECT Trash")
    assert c.cmd(b"m FETCH 1:* FLAGS") == [b"m OK FETCH completed\r\n"]


# ------------------------------------------------------------------------ STORE
def test_store_flags_and_keywords(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    assert ok(c.store("1", "+FLAGS", "(\\Seen \\Flagged)")) == [b"1 (FLAGS (\\Flagged \\Seen))"]
    assert ok(c.store("1", "-FLAGS", "\\Flagged")) == [b"1 (FLAGS (\\Seen))"]
    assert ok(c.store("1", "+FLAGS", "(urgent $Label1 \\Recent)")) == [
        b"1 (FLAGS (\\Seen $Label1 urgent))"]  # fmt: skip
    assert ok(c.store("1", "FLAGS", "(\\Answered urgent)")) == [b"1 (FLAGS (\\Answered urgent))"]
    assert ok(c.store("1:2", "+FLAGS.SILENT", "\\Draft")) == [None]
    assert ok(c.uid("STORE", "2", "-FLAGS", "(\\Draft)")) == [b"2 (UID 2 FLAGS ())"]
    m = proto.mailbox.get_message(proto.mailbox.list_messages(folder="inbox").items[0].id)
    assert sorted(m.flags) == ["ANSWERED", "DRAFT"] and "urgent" in m.labels
    ok(c.select("INBOX"))
    assert c.untagged_responses["FLAGS"] == [
        b"(\\Answered \\Flagged \\Deleted \\Seen \\Draft urgent)"]  # fmt: skip
    for bad in ("(\\Bogus)", '("a b")'):
        with pytest.raises(imaplib.IMAP4.error, match="invalid flag"):
            c.store("1", "+FLAGS", bad)
    with pytest.raises(imaplib.IMAP4.error, match="unknown STORE item"):
        c.store("1", "XFLAGS", "\\Seen")
    with pytest.raises(imaplib.IMAP4.error, match="usage"):
        c._simple_command("STORE", "1", "+FLAGS")
    assert ("agent", "flag", "ok") in proto.actions() and (
        "agent",
        "label",
        "ok",
    ) in proto.actions()


def test_other_sessions_see_flag_changes_and_new_mail(proto):
    a, b = proto.imap(), proto.imap()
    ok(a.select("INBOX"))
    ok(b.select("INBOX"))
    ok(a.store("2", "+FLAGS", "\\Flagged"))
    proto.ctl.inject(proto.run, proto.mailbox_id, {"from": "vendor@example.test", "to": [AGENT],
                     "subject": "new one", "text": "t"})  # fmt: skip
    ok(b.noop())
    assert b.untagged_responses["FETCH"] == [b"2 (UID 2 FLAGS (\\Flagged))"]
    assert b.untagged_responses["EXISTS"][-1] == b"3"
    # new mail is announced even during FETCH (EXISTS is always allowed)
    proto.ctl.inject(proto.run, proto.mailbox_id, {"from": "vendor@example.test", "to": [AGENT],
                     "subject": "another", "text": "t"})  # fmt: skip
    ok(b.fetch("1", "FLAGS"))
    assert b.untagged_responses["EXISTS"][-1] == b"4"


# ------------------------------------------------------------------------ COPY / MOVE / EXPUNGE
def test_copy_and_move_report_uids(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    typ, data = c.copy("1", "Archive")
    assert typ == "OK" and c.untagged_responses["COPYUID"][0].split()[1:] == [b"1", b"1"]
    typ, data = c.uid("COPY", "1:2", "INBOX")  # copy into the selected folder: EXISTS
    assert typ == "OK" and c.untagged_responses["EXISTS"][-1] == b"4"
    assert c.copy("1", "Nope")[0] == "NO" and b"TRYCREATE" in c.copy("1", "Nope")[1][0]
    ok(c.uid("COPY", "100", "Archive"))  # nothing matched: plain OK
    typ, data = c._simple_command("MOVE", "1", "Trash")
    assert typ == "OK"
    assert c.untagged_responses["EXPUNGE"] == [b"1"]
    assert c.untagged_responses["COPYUID"][0].split()[1:] == [b"1", b"1"]
    typ, data = c.uid("MOVE", "2", "Archive")
    assert typ == "OK" and c.untagged_responses["EXPUNGE"][-1] == b"1"
    assert c.uid("MOVE", "3", "Nope")[0] == "NO"
    with pytest.raises(imaplib.IMAP4.error, match="usage"):
        c.copy("1", "")[0] if False else c._simple_command("COPY", "1")
    ok(c.select("Archive"))
    uids = ok(c.uid("SEARCH", "ALL"))[0].split()
    assert uids == [b"1", b"2"]  # the move gave the message a new UID in Archive


def test_expunge_keeps_uids_stable(proto):
    for i in range(3):
        proto.ctl.inject(proto.run, proto.mailbox_id, {"from": "vendor@example.test",
                         "to": [AGENT], "subject": f"m{i}", "text": "t"})  # fmt: skip
    c = proto.imap()
    ok(c.select("INBOX"))
    assert ok(c.uid("SEARCH", "ALL")) == [b"1 2 3 4 5"]
    ok(c.store("2,4", "+FLAGS", "\\Deleted"))
    assert ok(c.expunge()) == [b"4", b"2"]  # descending: each number valid when sent
    assert ok(c.uid("SEARCH", "ALL")) == [b"1 3 5"]
    assert ok(c.search(None, "ALL")) == [b"1 2 3"]
    ok(c.expunge())  # nothing flagged: no engine write
    ok(c.uid("STORE", "1,5", "+FLAGS", "\\Deleted"))
    ok(c.uid("EXPUNGE", "5"))
    assert ok(c.uid("SEARCH", "ALL")) == [b"1 3"]
    d = proto.imap()  # a second session sees the same UIDs and UIDVALIDITY
    ok(d.select("INBOX"))
    assert ok(d.uid("SEARCH", "ALL")) == [b"1 3"]
    assert ok(d.uid("SEARCH", "DELETED")) == [b"1"]
    ok(c.close())  # CLOSE expunges silently
    ok(d.noop())
    assert d.untagged_responses["EXPUNGE"] == [b"1"]
    ok(d.unselect()) if hasattr(d, "unselect") else ok(d._simple_command("UNSELECT"))
    assert d.state == "AUTH"
    assert ("agent", "delete", "ok") in proto.actions()


def test_vanished_messages_stay_ghosts_until_a_full_sync(proto):
    a, b = proto.imap(), proto.imap()
    ok(a.select("INBOX"))
    ok(b.select("INBOX"))
    ok(a.store("1", "+FLAGS", "\\Deleted"))
    ok(a.expunge())
    assert fetch_map(ok(b.fetch("1:2", "UID")))  # non-UID FETCH: no EXPUNGE sent
    assert "EXPUNGE" not in b.untagged_responses
    assert ok(b.search(None, "ALL")) == [b"2"]
    ok(b.check())
    assert b.untagged_responses["EXPUNGE"] == [b"1"]


# ------------------------------------------------------------------------ APPEND
def test_append(proto):
    c = proto.imap()
    ok(c.select("Drafts"))
    typ, data = c.append("Drafts", "(\\Seen \\Draft keep)", '"15-Oct-2026 10:00:00 +0000"', RAW)
    assert typ == "OK" and c.untagged_responses["APPENDUID"][0].split()[1] == b"1"
    assert c.untagged_responses["EXISTS"][-1] == b"1"
    flags = fetch_map(ok(c.fetch("1", "(FLAGS BODY.PEEK[])")))[1]
    assert b"FLAGS (\\Seen \\Draft keep)" in flags and RAW in flags  # stored octets verbatim
    assert ok(c.append("INBOX", None, None, RAW))
    assert c.append("Nope", None, None, RAW)[0] == "NO"
    typ, data = c.append("INBOX", None, None, b"Subject: no sender\r\n\r\nx\r\n")
    assert typ == "NO" and b"INVALID_REQUEST" in data[0]
    with pytest.raises(imaplib.IMAP4.error, match="invalid flag"):
        c.append("INBOX", "(\\Bogus)", None, RAW)
    raw = proto.raw()
    raw.cmd(f"a LOGIN {AGENT} {proto.key}".encode())
    assert raw.cmd(b"b APPEND INBOX")[-1].startswith(b"b BAD usage")
    assert raw.cmd(b"c APPEND INBOX NIL {1+}\r\nx")[-1].startswith(b"c BAD invalid APPEND")
    assert ("agent", "insert", "ok") in proto.actions()


# ------------------------------------------------------------------------ SEARCH
@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (["ALL"], b"1 2"),
        (["UNSEEN"], b"1 2"),
        (["SEEN"], b""),
        (["FLAGGED"], b"2"),
        (["UNFLAGGED"], b"1"),
        (["KEYWORD", "urgent"], b"2"),
        (["UNKEYWORD", "urgent"], b"1"),
        (["FROM", "vendor"], b"1"),
        (["FROM", '"Example Vendor"'], b"1"),
        (["TO", "finance@"], b"1 2"),
        (["CC", "x"], b""),
        (["BCC", "x"], b""),
        (["SUBJECT", '"invoice inv-001"'], b"1 2"),
        (["BODY", "purchase"], b"2"),
        (["TEXT", "procurement@"], b"2"),
        (["TEXT", "INR"], b"1"),
        (["HEADER", "Message-ID", "po-001"], b"2"),
        (["HEADER", "In-Reply-To", '""'], b"2"),
        (["SINCE", "15-Oct-2026"], b"1 2"),
        (["BEFORE", "15-oct-2026"], b""),
        (["ON", "15-Oct-2026"], b"1 2"),
        (["SENTON", "15-Oct-2026"], b"1 2"),
        (["SENTBEFORE", "16-Oct-2026"], b"1 2"),
        (["SENTSINCE", "16-Oct-2026"], b""),
        (["LARGER", "400"], b"1"),
        (["SMALLER", "400"], b"2"),
        (["UID", "2:*"], b"2"),
        (["NOT", "FROM", "vendor"], b"2"),
        (["OR", "FLAGGED", "FROM", "vendor"], b"1 2"),
        (["(FLAGGED KEYWORD urgent)"], b"2"),
        (["2"], b"2"),
        (["1:*", "NOT", "2"], b"1"),
        (["RECENT"], b""),
        (["NEW"], b""),
        (["OLD"], b"1 2"),
        (["ANSWERED"], b""),
        (["DELETED"], b""),
        (["UNDRAFT"], b"1 2"),
        (["CHARSET", "UTF-8", "ALL"], b"1 2"),
    ],
)
def test_search(proto, criteria, expected):
    c = proto.imap()
    ok(c.select("INBOX"))
    ok(c.store("2", "+FLAGS", "(\\Flagged urgent)"))
    assert ok(c.search(None, *criteria)) == [expected]


def test_search_objectids_and_errors(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    first = proto.mailbox.list_messages(folder="inbox").items[0]
    assert ok(c.uid("SEARCH", "EMAILID", first.id)) == [b"1"]
    assert ok(c.uid("SEARCH", "THREADID", first.thread_id)) == [b"1 2"]
    assert c.search("KOI8-R", "ALL")[0] == "NO"
    for bad, err in [(("BOGUS",), "unknown search key"), (("FROM",), "needs an argument"),
                     (("SINCE", "yesterday"), "invalid date"), (("ON", "31-Feb-2026"), "invalid date"),
                     (("LARGER", "big"), "needs a number"), (("()",), "empty search list"),
                     (('"ALL"',), "invalid search key")]:  # fmt: skip
        with pytest.raises(imaplib.IMAP4.error, match=err):
            c.search(None, *bad)
    with pytest.raises(imaplib.IMAP4.error, match="usage"):
        c._simple_command("SEARCH")


# ------------------------------------------------------------------------ guards, budget, reset
def test_engine_permissions_apply(make_proto):
    def read_only(s):
        s["agent"]["allowed_operations"] = ["read", "search", "reply", "move"]
        s["agent"]["prohibited_operations"] = ["external_delivery"]

    p = make_proto(read_only)
    c = p.imap()
    ok(c.select("INBOX"))
    typ, data = c.store("1", "+FLAGS", "\\Seen")
    assert typ == "NO" and data[0].startswith(b"[NOPERM] PERMISSION_DENIED")
    ok(c.uid("MOVE", "1", "Archive"))  # move is allowed by this scenario


def test_budget_limit(make_proto):
    def tiny(s):
        open_agent(s)
        s["evaluation"]["limits"]["max_agent_actions"] = 1

    p = make_proto(tiny)
    c = p.imap()
    ok(c.select("INBOX"))
    ok(c.store("1", "+FLAGS", "\\Seen"))
    typ, data = c.store("1", "+FLAGS", "\\Flagged")
    assert typ == "NO" and data[0] == b"[LIMIT] QUOTA_EXCEEDED: action limit reached"


def test_reset_changes_uidvalidity(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    before = c.untagged_responses["UIDVALIDITY"][0]
    proto.ctl.reset_run(proto.run)
    proto.ctl.start_run(proto.run)
    with pytest.raises(imaplib.IMAP4.abort):
        c.noop()
    d = proto.imap()
    ok(d.select("INBOX"))
    assert int(d.untagged_responses["UIDVALIDITY"][0]) > int(before)


def test_reads_are_audited_without_spending_budget(proto):
    c = proto.imap()
    ok(c.select("INBOX"))
    ok(c.fetch("1:*", "(FLAGS BODY.PEEK[])"))
    acts = [a for a in proto.ctl.actions(proto.run) if a["actor"] == "agent"]
    assert acts
    assert {a["operation"] for a in acts} == {"read"}
    assert proto.ctl.run_info(proto.run).get("agent_actions", 0) == 0
