"""Graph `$search` KQL subset: parsing, explicit refusals and matching (pure unit tests)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from mailsim.adapters.graph import kql
from mailsim.adapters.graph.odata import QueryError
from mailsim.domain.models import Address, AttachmentRef, CanonicalMessage

T0 = datetime(2026, 10, 15, 8, 45, tzinfo=UTC)


def msg(**kw) -> CanonicalMessage:
    data = dict(
        id="msg_1", mailbox_id="mbx", message_id_header="<1@x>", thread_id="thr",
        folder="INBOX", from_=Address(email="vendor@example.test", name="Vendor Desk"),
        to=[Address(email="finance@example.test")], cc=[Address(email="cc@example.test")],
        bcc=[Address(email="hidden@example.test")], subject="Invoice INV-001",
        body_text="Please update the bank details", body_html="<p>urgent</p>",
        created_at=T0, received_at=T0, flags=["SEEN"],
        attachments=[AttachmentRef(id="att_1", filename="INV-001.pdf",
                                   mime_type="application/pdf", size=1, checksum="c",
                                   blob_ref="b")],
    )  # fmt: skip
    data.update(kw)
    return CanonicalMessage(**data)


def hit(q: str, m: CanonicalMessage | None = None) -> bool:
    s = kql.parse(q)
    assert s.node is not None
    return kql.predicate(s.node)(m or msg())


def test_plain_text_keeps_the_substring_search():
    assert kql.parse('"bank details"') == kql.Search("bank details", None, None)
    assert kql.parse("hello world").text == "hello world"
    assert kql.parse("Re: invoice and more").text == "Re: invoice and more"
    assert kql.parse("http://x.test/a").text == "http://x.test/a"
    assert kql.parse("a=b c<d").text == "a=b c<d"
    assert kql.parse('"say \\"hi\\""').text == 'say "hi"'


@pytest.mark.parametrize(("query", "expected"), [
    ("subject:invoice", True), ("SUBJECT:INVOICE", True), ("subject:nope", False),
    ('subject:"INV-001"', True), ("body:urgent", True), ("body:bank", True),
    ("from:vendor", True), ("from:desk", True), ("to:finance", True), ("cc:cc@", True),
    ("bcc:hidden", True), ("participants:hidden", True), ("participants:nobody", False),
    ("attachment:inv-001", True), ("attachment:docx", False),
    ("hasAttachments:true", True), ("hasAttachment:false", False), ("isRead:true", True),
    ("isRead:false", False), ("received:2026-10-15", True), ("received=2026-10-15", True),
    ("received:2026-10-14", False), ("received>=2026-10-15", True), ("received>2026-10-15", False),
    ("received<2026-10-16", True), ("received<=2026-10-14", False),
    ("received:2026-10-01..2026-10-15", True), ("received:2026-10-16..2026-10-20", False),
    ("received>=2026-10-15T08:45:00Z", True), ("received>2026-10-15T08:45:00", False),
    ("subject:invoice AND from:vendor", True), ("subject:invoice from:nobody", False),
    ("subject:nope OR from:vendor", True), ("NOT isRead:true", False), ("-isRead:false", True),
    ("(subject:nope OR body:urgent) AND hasAttachments:true", True),
    ('bank subject:"invoice"', True), ('"bank" OR "nothing"', True), ("missing subject:x", False),
])  # fmt: skip
def test_matching(query, expected):
    assert hit(query) is expected


def test_received_falls_back_to_created_at():
    assert hit("received:2026-10-15", msg(received_at=None))


def test_anchor_is_a_positive_top_level_free_text_term():
    assert kql.parse("bank subject:x").anchor == "bank"
    assert kql.parse("subject:x AND bank").anchor == "bank"
    assert kql.parse("subject:x").anchor is None
    assert kql.parse("bank OR subject:x").anchor is None
    assert kql.parse("NOT bank subject:x").anchor is None


@pytest.mark.parametrize(("query", "message"), [
    ("foo:bar", "unsupported property"), ("subject:", "missing value"),
    ("subject<x", "takes ':'"), ("isRead:maybe", "true or false"),
    ("received:yesterday", "invalid date"), ("received:2026-13-01", "invalid date"),
    ('subject:"open', "unterminated quote"), ('""', "empty $search"), ('subject:x " "', "empty phrase"),
    ("(subject:x", "missing ')'"), ("subject:x)", "unexpected ')'"), ("subject:x OR", "expected"),
    ("NOT", "unexpected None"), ('foo="x"', "unexpected"), ("x" * 1025, "longer than"),
    ("(" * 40 + "subject:x" + ")" * 40, "nesting"), ("NOT " * 40 + "subject:x", "nesting"),
])  # fmt: skip
def test_refusals(query, message):
    with pytest.raises(QueryError, match=re.escape(message)):
        kql.parse(query)
