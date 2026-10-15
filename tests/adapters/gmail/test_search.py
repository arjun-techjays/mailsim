"""Gmail search subset: parser (unit + property tests) and predicates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mailsim.adapters.gmail.search import (
    And,
    Context,
    Not,
    Or,
    Term,
    includes_trash,
    matches,
    parse_date,
    parse_query,
    render,
    terms,
)
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import Address, AttachmentRef, CanonicalMessage

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


def msg(**kw) -> CanonicalMessage:
    base = dict(id="m1", mailbox_id="mb", message_id_header="<a@x.test>", thread_id="t",
                folder="INBOX", from_=Address(email="vendor@example.test", name="Example Vendor"),
                to=[Address(email="finance@example.test")], cc=[Address(email="cc@example.test")],
                bcc=[Address(email="hidden@example.test")], subject="Invoice INV-001",
                body_text="Please pay the purchase order", created_at=NOW - timedelta(days=3),
                labels=["INBOX", "Vendors/Acme", "UNREAD"], flags=["FLAGGED"])  # fmt: skip
    base.update(kw)
    return CanonicalMessage(**base)


CTX = Context(lambda: "finance@example.test", lambda: NOW)


def hit(q: str, m: CanonicalMessage | None = None) -> bool:
    return matches(parse_query(q), m or msg(), CTX)


@pytest.mark.parametrize(("q", "expected"), [
    ("", True), ("   ", True),
    ("invoice", True), ("INVOICE", True), ("purchase order", True), ('"purchase order"', True),
    ('"order purchase"', False), ("vendor@example", True), ("missing", False),
    ("from:vendor", True), ("from:Acme", False), ("from:me", False), ("to:me", True),
    ("from:(vendor example)", True), ("from:(vendor nobody)", False),
    ("cc:cc@", True), ("bcc:hidden", True), ("to:nobody", False),
    ('subject:"INV-001"', True), ("subject:payment", False),
    ("label:vendors-acme", True), ("label:Vendors/Acme", True), ("label:other", False),
    ("label:inbox", True), ("label:unread", True), ("label:starred", True), ("label:read", False),
    ("label:trash", False), ("in:inbox", True), ("in:sent", False), ("in:anywhere", True),
    ("in:spam", False), ("in:archive", False), ("in:drafts", False),
    ("is:unread", True), ("is:read", False), ("is:starred", True), ("is:unstarred", False),
    ("is:draft", False), ("has:attachment", False), ("has:userlabels", True),
    ("has:nouserlabels", False), ("filename:pdf", False),
    ("after:2026/10/01", True), ("after:2026/10/14", False), ("before:2026-10-14", True),
    ("newer:2026/10/01", True), ("older:2026/10/01", False), (f"after:{int(NOW.timestamp())}", False),
    ("older_than:2d", True), ("older_than:1y", False), ("newer_than:1m", True), ("newer_than:1d", False),
    ("rfc822msgid:<a@x.test>", True), ("rfc822msgid:a@x.test", True), ("rfc822msgid:b", False),
    ("invoice -missing", True), ("-invoice", False), ("--invoice", True),
    ("missing OR invoice", True), ("missing OR nothing", False), ("{missing invoice}", True),
    ("{missing}", False), ("(missing OR invoice) from:vendor", True), ("invoice AND from:vendor", True),
    ("NOT_AN_OP:value", False), ("re: invoice", False), ("- invoice", True), ("- missing", False),
])  # fmt: skip
def test_queries(q, expected):
    assert hit(q) is expected


def test_attachment_and_draft_predicates():
    att = AttachmentRef(id="a", filename="INV-001.pdf", mime_type="application/pdf", size=1,
                        checksum="c", blob_ref="b")  # fmt: skip
    m = msg(attachments=[att], folder="Drafts", labels=["DRAFTS"], is_draft=True, flags=["SEEN"],
            body_text=None, received_at=NOW)  # fmt: skip
    assert hit("has:attachment filename:pdf filename:inv-001 in:drafts is:draft is:read", m)
    assert hit("has:nouserlabels -is:unread newer_than:1d", m)


@pytest.mark.parametrize("q", [
    '"unterminated', '""', "from:", "from: x", "in:chats", "is:important", "has:drive",
    "after:yesterday", "older_than:5w", "larger:10M", "category:social", "a AROUND b",
    "(a", "a)", "{a", "{}", "OR a", "a OR", "()", "from:(a", "from:()", "label_id:x",
    "deliveredto:x", "-OR", "a -)",
])  # fmt: skip
def test_rejected_queries(q):
    with pytest.raises(InvalidRequest):
        parse_query(q)


def test_terms_and_trash_detection():
    node = parse_query("a (b OR c) -d in:trash")
    assert [(t.value, top) for t, top in terms(node)] == [
        ("a", True), ("b", False), ("c", False), ("d", False), ("trash", True)]  # fmt: skip
    assert includes_trash(node) and includes_trash(parse_query("x OR in:anywhere"))
    assert not includes_trash(parse_query("label:inbox")) and terms(None) == []
    assert parse_date("2026-1-5") == datetime(2026, 1, 5, tzinfo=UTC)


# ------------------------------------------------------------------------ property tests

WORD = st.text(alphabet="abcxyz019_.@", min_size=1, max_size=6).filter(
    lambda w: w.upper() not in ("OR", "AND", "AROUND"))  # fmt: skip
OPS = ["from", "to", "cc", "bcc", "subject", "label", "filename", "rfc822msgid"]
LEAF = st.one_of(
    st.builds(Term, st.none(), WORD),
    st.builds(Term, st.none(), WORD.map(lambda w: f"{w} {w}"), st.just(True)),
    st.builds(Term, st.sampled_from(OPS), WORD),
    st.builds(Term, st.just("is"), st.sampled_from(["unread", "read", "starred"])),
    st.builds(Term, st.just("in"), st.sampled_from(["inbox", "trash", "anywhere"])),
)
NODES = st.recursive(LEAF, lambda kids: st.one_of(
    st.builds(Not, kids),
    st.lists(kids, min_size=2, max_size=3).map(lambda xs: And(tuple(xs))),
    st.lists(kids, min_size=2, max_size=3).map(lambda xs: Or(tuple(xs))),
), max_leaves=8)  # fmt: skip


def _flat(node):
    """Normalise nesting the grammar cannot express (And inside And etc.)."""
    if isinstance(node, Not):
        return Not(_flat(node.node))
    if isinstance(node, And | Or):
        out = []
        for child in map(_flat, node.nodes):
            out += child.nodes if type(child) is type(node) else [child]
        return type(node)(tuple(out))
    return node


@settings(max_examples=300, deadline=None)
@given(NODES)
def test_render_parse_roundtrip(node):
    assert _flat(parse_query(render(node))) == _flat(node)


@settings(max_examples=200, deadline=None)
@given(NODES)
def test_negation_and_disjunction_semantics(node):
    m = msg()
    q = render(node)
    assert hit(f"-({q})", m) is (not hit(q, m))
    assert hit(f"({q}) OR missing", m) is hit(q, m)
    assert hit(f"({q}) invoice", m) is hit(q, m)


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=40))
def test_arbitrary_input_parses_or_is_rejected_cleanly(text):
    try:
        node = parse_query(text)
    except InvalidRequest:
        return
    matches(node, msg(), CTX)  # never raises once parsed
