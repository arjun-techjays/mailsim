"""IMAP and the per-run resource caps (issue #7): keyword lists are capped per command like
every other interface's label lists (refused before anything is written), the per-message
label cap applies to keywords, and a read cap reached during a command ends it with
`NO [LIMIT]`."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.store import models as m
from tests.protocols.conftest import AGENT, open_agent

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


def test_read_cap_ends_a_command_with_no_limit(make_proto):
    proto = make_proto(_with(max_agent_reads=40))
    c = proto.raw()
    assert c.cmd(f"a1 LOGIN {AGENT} {proto.key}".encode())[-1].startswith(b"a1 OK")
    assert c.cmd(b"a2 SELECT INBOX")[-1].startswith(b"a2 OK")
    n = 2
    while _reads(proto) < 39:  # each NOOP polls the change feed: one read
        n += 1
        assert c.cmd(b"a%d NOOP" % n)[-1].startswith(b"a%d OK" % n)
    lines = c.cmd(b"f1 FETCH 1:2 (BODY.PEEK[])")
    assert lines[-1] == b"f1 NO [LIMIT] QUOTA_EXCEEDED: action limit reached\r\n"
    assert _reads(proto) == 40
    assert c.cmd(b"z1 LOGOUT")[0].startswith(b"* BYE")


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
