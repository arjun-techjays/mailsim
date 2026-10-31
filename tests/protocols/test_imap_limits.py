"""IMAP and the per-run resource caps (issue #7): keyword lists are capped per command like
every other interface's label lists (refused before anything is written), the per-message
label cap applies to keywords, and reads are budgeted per command (issue #24): a command at
the read cap answers `NO [LIMIT]`, a command under it completes whatever it reads."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.store import models as m
from tests.protocols.conftest import AGENT, VENDOR, open_agent

pytestmark = pytest.mark.db

RAW = (f"From: {AGENT}\r\nTo: vendor@example.test\r\nSubject: filed\r\n"
       "Message-ID: <kw-append@example.test>\r\n\r\nbody\r\n").encode()  # fmt: skip


def _with(**limits: int) -> Any:
    def mutate(s: dict[str, Any]) -> None:
        open_agent(s)
        s["evaluation"]["limits"].update(limits)

    return mutate


def _reads(proto) -> int:
    with proto.env.session_factory() as s:
        return s.get(m.Run, proto.run).agent_reads


def _inbox(proto) -> int:
    return len(proto.mailbox.list_messages(folder="inbox").items)


def test_keyword_lists_over_the_cap_are_refused_before_any_write(proto):
    c = proto.imap()
    c.select("INBOX")
    before = proto.actions()
    many = "(" + " ".join(f"k{i}" for i in range(51)) + ")"
    typ, data = c.store("1", "+FLAGS", many)
    assert typ == "NO" and data[0].startswith(b"[CANNOT] INVALID_REQUEST: at most 50 keywords")
    typ, data = c.store("1", "+FLAGS", "(" + "k" * 226 + ")")
    assert typ == "NO" and b"at most 225 characters" in data[0]
    inbox = _inbox(proto)
    typ, data = c.append("INBOX", many, None, RAW)
    assert typ == "NO" and data[0].startswith(b"[CANNOT] INVALID_REQUEST: at most 50 keywords")
    assert _inbox(proto) == inbox  # nothing was stored
    after = proto.actions()[len(before) :]
    assert not [a for a in after if a[1] in ("label", "insert")]  # nothing written or audited
    # exactly the cap is fine (the same 50 keywords twice count once)
    fifty = "(" + " ".join(f"k{i}" for i in range(50)) + " k0)"
    assert c.store("1", "+FLAGS.SILENT", fifty)[0] == "OK"


def test_per_message_cap_applies_to_keywords(make_proto):
    proto = make_proto(_with(max_labels_per_message=2))
    c = proto.imap()
    c.select("INBOX")
    assert c.store("1", "+FLAGS", "(a b)")[0] == "OK"
    typ, data = c.store("1", "+FLAGS", "(c)")
    assert typ == "NO" and data[0] == b"[LIMIT] QUOTA_EXCEEDED: action limit reached"
    assert c.store("1", "-FLAGS", "(a)")[0] == "OK"  # removing is never capped
    # APPEND with more keywords than the cap: refused before the message is stored
    inbox = _inbox(proto)
    typ, data = c.append("INBOX", "(x y z)", None, RAW)
    assert typ == "NO" and data[0].startswith(b"[LIMIT] QUOTA_EXCEEDED")
    assert _inbox(proto) == inbox
    assert ("agent", "insert", "SIMULATION_LIMIT_EXCEEDED") in proto.actions()
    assert c.append("INBOX", "(x y)", None, RAW)[0] == "OK"  # within the cap: stored
    assert _inbox(proto) == inbox + 1


def test_read_cap_refuses_the_next_command_before_any_response(make_proto):
    proto = make_proto(_with(max_agent_reads=40))
    c = proto.raw()
    assert c.cmd(f"a1 LOGIN {AGENT} {proto.key}".encode())[-1].startswith(b"a1 OK")
    assert c.cmd(b"a2 SELECT INBOX")[-1].startswith(b"a2 OK")
    n = 2
    while _reads(proto) < 40:  # each NOOP polls the change feed: one read
        n += 1
        assert c.cmd(b"a%d NOOP" % n)[-1].startswith(b"a%d OK" % n)
    lines = c.cmd(b"f1 FETCH 1:2 (BODY.PEEK[])")
    assert lines == [b"f1 NO [LIMIT] QUOTA_EXCEEDED: action limit reached\r\n"]
    assert _reads(proto) == 40
    assert c.cmd(b"z1 LOGOUT")[0].startswith(b"* BYE")


def _bulk(proto, n: int) -> None:
    for i in range(n):
        proto.ctl.inject(proto.run, proto.mailbox_id, {"from": VENDOR, "to": [AGENT],
                                                       "subject": f"bulk {i}", "text": "t"})  # fmt: skip


def test_fetch_of_a_whole_mailbox_is_one_read(make_proto):
    """Issue #24: reads are budgeted per command, like one Gmail/REST page. A FETCH of every
    body (its change-feed poll, the listing and one engine read per message, plus the \\Seen
    side effects) costs one read, so a client can sync a mailbox far larger than its read cap.
    Every engine read is still in the audit, and the cap still refuses the next command."""
    proto = make_proto(_with(max_agent_reads=3))
    _bulk(proto, 120)
    c = proto.imap()  # LOGIN: no read
    typ, data = c.select("INBOX")  # read 1
    assert typ == "OK"
    total = int(data[0])
    assert total >= 120 and _reads(proto) == 1
    before = len(proto.actions())
    typ, data = c.fetch("1:*", "(UID BODY[])")  # read 2: every body, marking each seen
    assert typ == "OK"
    assert len([d for d in data if isinstance(d, tuple)]) == total
    assert _reads(proto) == 2
    audited = [a for a in proto.actions()[before:] if a[1] == "read"]
    assert len(audited) > total and all(a == ("agent", "read", "ok") for a in audited)
    typ, data = c.uid("SEARCH", "SEEN")  # read 3
    assert typ == "OK" and len(data[0].split()) == total
    assert _reads(proto) == 3
    typ, data = c.fetch("1:*", "(BODY.PEEK[])")  # the cap: refused, nothing read
    assert typ == "NO" and data[0].startswith(b"[LIMIT] QUOTA_EXCEEDED")
    assert c.noop()[0] == "NO"
    assert _reads(proto) == 3
    assert ("agent", "read", "SIMULATION_LIMIT_EXCEEDED") in proto.actions()


def test_smtp_transaction_is_one_read(make_proto):
    """An SMTP submission looks up the replied message (In-Reply-To, else each References
    entry): one read for the transaction, however many lookups it makes."""
    proto = make_proto(_with(max_agent_reads=100))
    before = _reads(proto)
    lookups = len([a for a in proto.actions() if a[1] == "read"])
    refs = " ".join(f"<gone{i}@example.test>" for i in range(5))
    s = proto.smtp()
    s.sendmail(AGENT, [VENDOR], (f"From: {AGENT}\r\nTo: {VENDOR}\r\nSubject: re\r\n"
                                 f"References: {refs}\r\n\r\nx").encode())  # fmt: skip
    assert len([a for a in proto.actions() if a[1] == "read"]) - lookups >= 5
    assert _reads(proto) == before + 1


def test_writes_after_the_read_cap_are_refused_cleanly_or_succeed(make_proto):
    """STORE and APPEND re-sync the session after their write: those read-backs may not turn a
    committed write into NO [LIMIT]. Walking up to and past the cap, each command either
    succeeds with its effect or is refused without one."""
    proto = make_proto(_with(max_agent_reads=30))
    c = proto.imap()
    c.select("INBOX")
    target = proto.mailbox.list_messages(folder="inbox").items[0].id

    for i in range(40):
        store_typ, _ = c.store("1", "+FLAGS.SILENT", f"(s{i})")
        msg = next(x for x in proto.ev.state(proto.run)["messages"] if x["id"] == target)
        assert (f"s{i}" in msg["labels"]) == (store_typ == "OK")
        count = len(proto.ev.state(proto.run)["messages"])
        append_typ, _ = c.append("INBOX", None, None, RAW)
        assert len(proto.ev.state(proto.run)["messages"]) == count + (append_typ == "OK")
        if store_typ == "NO" and append_typ == "NO":
            return
    pytest.fail("the read cap never refused a command")


def test_login_and_smtp_work_at_the_read_cap(make_proto):
    """The mailbox check at LOGIN / AUTH is not an agent read (unaudited, never capped): an
    agent at its read cap can still log in and send over SMTP, as over REST/Gmail/Graph/MCP.
    Reads it then asks for are refused."""
    proto = make_proto(_with(max_agent_reads=1))
    proto.mailbox.list_folders()  # the one read
    before = proto.actions()
    c = proto.imap()  # LOGIN succeeds
    typ, data = c.select("INBOX")  # SELECT reads: refused
    assert typ == "NO" and data[0].startswith(b"[LIMIT] QUOTA_EXCEEDED")
    s = proto.smtp()  # AUTH succeeds
    s.sendmail(
        AGENT,
        ["vendor@example.test"],
        b"From: finance@example.test\r\nTo: vendor@example.test\r\nSubject: at cap\r\n\r\nx",
    )
    after = proto.actions()[len(before) :]
    assert ("agent", "send", "ok") in after
    assert [a for a in after if a[1] == "read" and a[2] == "ok"] == []  # logins read nothing
    assert _reads(proto) == 1
