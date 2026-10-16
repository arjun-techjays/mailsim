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


def test_one_word_or_phrase_is_one_substring_search():
    # Graph quotes the whole $search value; a phrase inside it is escaped as \"
    assert kql.parse('"\\"bank details\\""') == kql.Search("bank details", None, None)
    assert kql.parse('"invoice"') == kql.Search("invoice", None, None)
    assert kql.parse("http://x.test/a").text == "http://x.test/a"
    assert kql.parse("re:meeting").text == "re:meeting"  # unknown prefix: free text
    assert kql.parse("mailto:a@x").text == "mailto:a@x"
    assert kql.parse('foo:"x y"').text == "foo:x y"


def test_several_free_text_terms_are_anded():
    two = kql.parse('"bank details"')  # outer quotes only: two words
    assert two.text is None and two.anchor == "bank"
    assert hit('"bank details"') and not hit('"bank nothing"')
    assert hit("hello world", msg(subject="world", body_text="hello")) is True
    assert hit('"\\"update the\\" bank"') and not hit('"\\"the update\\" bank"')
    assert hit("Re: invoice", msg(subject="Re: invoice")) and hit("a=b c<d", msg(subject="a=b c<d"))


@pytest.mark.parametrize(("query", "expected"), [
    ("subject:invoice", True), ("SUBJECT:INVOICE", True), ("subject:nope", False),
    ('subject:"INV-001"', True), ("body:urgent", True), ("body:bank", True),
    ("from:vendor", True), ("from:desk", True), ("to:finance", True), ("cc:cc@", True),
    ("bcc:hidden", True), ("participants:hidden", True), ("participants:nobody", False),
    ("recipients:hidden", True), ("recipients:vendor", False), ("body:<p>", False),
    ("sent:2026-10-15", True), ("sent<2026-10-15", False), ("foo:bar subject:invoice", False),
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


def test_received_falls_back_to_created_at_and_drafts_have_no_sent_date():
    assert hit("received:2026-10-15", msg(received_at=None))
    assert not hit("sent:2026-10-15", msg(is_draft=True))


def test_anchor_is_a_positive_top_level_free_text_term():
    assert kql.parse("bank subject:x").anchor == "bank"
    assert kql.parse("subject:x AND bank").anchor == "bank"
    assert kql.parse("subject:x").anchor is None
    assert kql.parse("bank OR subject:x").anchor is None
    assert kql.parse("NOT bank subject:x").anchor is None


@pytest.mark.parametrize(("query", "message"), [
    ("subject:", "missing value"), ("received:9999-12-31", "invalid date"),
    ("received>9999-12-31T23:59:59.999999", "invalid date"), ("sent:x", "invalid date"),
    ("subject<x", "takes ':'"), ("isRead:maybe", "true or false"),
    ("received:yesterday", "invalid date"), ("received:2026-13-01", "invalid date"),
    ('subject:"open', "unterminated quote"), ('""', "empty $search"), ('subject:x " "', "empty phrase"),
    ("(subject:x", "missing ')'"), ("subject:x)", "unexpected ')'"), ("subject:x OR", "expected"),
    ("NOT", "unexpected None"), ("x" * 1025, "longer than"),
    ("(" * 40 + "subject:x" + ")" * 40, "nesting"), ("NOT " * 40 + "subject:x", "nesting"),
])  # fmt: skip
def test_refusals(query, message):
    with pytest.raises(QueryError, match=re.escape(message)):
        kql.parse(query)
