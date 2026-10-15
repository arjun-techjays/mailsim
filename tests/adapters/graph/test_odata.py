"""The OData subset (pure functions, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mailsim.adapters.graph import odata
from mailsim.adapters.graph.odata import QueryError, QueryNotSupported

DOC = {
    "isRead": False,
    "hasAttachments": True,
    "subject": "Invoice INV-001",
    "receivedDateTime": "2026-10-15T08:30:00Z",
    "sentDateTime": None,
    "from": {"emailAddress": {"address": "Vendor@Example.test", "name": "Vendor"}},
    "conversationId": "AAMkconv",
    "categories": ["Finance", "Urgent"],
    "toRecipients": [{"emailAddress": {"address": "finance@example.test", "name": "F"}}],
    "flag": {"flagStatus": "flagged"},
}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("isRead eq false", True),
        ("isRead ne false", False),
        ("hasAttachments eq true and isRead eq true", False),
        ("hasAttachments eq true or isRead eq true", True),
        ("(isRead eq true or hasAttachments eq true) and subject eq 'invoice inv-001'", True),
        ("from/emailAddress/address eq 'vendor@example.test'", True),
        ("from/emailAddress/name ne 'x'", True),
        ("subject eq 'it''s'", False),
        ("receivedDateTime ge 2026-10-15T00:00:00Z", True),
        ("receivedDateTime gt 2026-10-15T08:30:00Z", False),
        ("receivedDateTime le 2026-10-15", False),
        ("receivedDateTime lt '2026-10-16T00:00:00+00:00'", True),
        ("sentDateTime ge 2026-01-01T00:00:00Z", False),
        ("sentDateTime eq null", True),
        ("conversationId eq 'AAMkconv'", True),
        ("flag/flagStatus eq 'flagged'", True),
        ("startswith(subject,'inv')", True),
        ("startswith(subject, 'Re:')", False),
        ("categories/any(c:c eq 'urgent')", True),
        ("categories/all(c:c eq 'urgent')", False),
        ("toRecipients/any(r:r/emailAddress/address eq 'finance@example.test')", True),
        ("ccRecipients/any(r:r/emailAddress/address eq 'x@example.test')", False),
    ],
)
def test_filter_semantics(expr, expected):
    assert odata.compile_filter(expr)(DOC) is expected


@pytest.mark.parametrize(
    "expr",
    [
        "", "   ", "isRead", "isRead eq", "isRead eq true)", "(isRead eq true", "isRead eq 'x'",
        "isRead gt true", "subject eq true", "receivedDateTime ge 5", "subject eq #",
        "receivedDateTime ge 'not-a-date'", "receivedDateTime ge 2026-13-45T00:00:00Z",
        "5 eq isRead", "isRead foo true", "startswith(isRead,'x')", "startswith(subject,true)",
        "categories/any(c/x:c eq 'a')", "isRead eq true isRead",
    ],
)  # fmt: skip
def test_malformed_filters_are_invalid_requests(expr):
    with pytest.raises(QueryError):
        odata.compile_filter(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "bodyPreview eq 'x'", "not isRead eq true", "subject gt 'a'", "contains(subject,'x')",
        "replyTo/any(r:r eq 'x')", "categories/any(c:c/x eq 'a')", "isRead has true",
    ],
)  # fmt: skip
def test_unsupported_filters_are_not_supported(expr):
    with pytest.raises(QueryNotSupported):
        odata.compile_filter(expr)


def test_orderby_select_expand_and_ints():
    assert odata.parse_orderby("receivedDateTime desc,subject") == [
        ("receivedDateTime", True), ("subject", False)]  # fmt: skip
    for bad in ("", "subject sideways", "subject asc extra"):
        with pytest.raises(QueryError):
            odata.parse_orderby(bad)
    with pytest.raises(QueryNotSupported):
        odata.parse_orderby("bodyPreview")
    assert odata.parse_select("subject, isRead") == {"subject", "isRead"}
    with pytest.raises(QueryError):
        odata.parse_select("subject,nope")
    exp = odata.parse_expand(
        "attachments,singleValueExtendedProperties($filter=id eq 'Integer   0x1081')"
    )
    assert exp.attachments and exp.extended_ids == {"integer 0x1081"}
    with pytest.raises(QueryNotSupported):
        odata.parse_expand("mentions")
    assert odata.parse_int("$top", None, default=7, low=1, high=9) == 7
    assert odata.parse_int("$top", "3", default=7, low=1, high=9) == 3
    for raw in ("x", "-1", "10"):
        with pytest.raises(QueryError):
            odata.parse_int("$top", raw, default=7, low=1, high=9)
    assert odata.parse_search('"purchase order"') == "purchase order"
    assert odata.parse_search("plain") == "plain"
    with pytest.raises(QueryError):
        odata.parse_search('""')


def test_tokens_and_helpers():
    token = odata.encode_token({"s": 5, "m": "c"})
    assert odata.decode_token(token, ("s",)) == {"s": 5, "m": "c"}
    for bad in ("%%%", odata.encode_token({"s": -1}), odata.encode_token({"s": True}),
                odata.encode_token({"x": 1}), "WzFd"):  # fmt: skip
        with pytest.raises(QueryError):
            odata.decode_token(bad, ("s",))
    assert odata.path_get({"a": {"b": 1}}, "a/b") == 1
    assert odata.path_get({"a": 1}, "a/b") is None
    assert odata.path_get(5, "") == 5
    assert odata.parse_datetime("2026-10-15T08:30:00") == datetime(2026, 10, 15, 8, 30, tzinfo=UTC)
