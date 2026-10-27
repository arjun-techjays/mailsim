from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import Address
from mailsim.mime.build import build_mime
from mailsim.mime.limits import MimeLimits
from mailsim.mime.parse import ParsedAttachment, parse_mime, sanitize_filename
from tests import helpers as h


def test_plain_message_fields():
    p = parse_mime(h.PLAIN)
    assert p.message_id == "<invoice-001@example.test>"
    assert p.from_ == Address(email="vendor@example.test", name="Example Vendor")
    assert [a.email for a in p.to] == ["finance@example.test"]
    assert p.cc[0].name == "Procurement"
    assert p.subject == "Invoice INV-001"
    assert p.body_text == "Please confirm the payment date.\n"
    assert p.body_html is None and p.attachments == [] and p.warnings == []
    assert p.date == datetime(2026, 10, 15, 9, tzinfo=UTC)
    assert ("Subject", "Invoice INV-001") in p.headers


def test_reply_headers_preserved():
    p = parse_mime(h.REPLY)
    assert p.in_reply_to == "<invoice-001@example.test>"
    assert p.references == ["<invoice-001@example.test>"]


def test_folded_references_and_multiple_ids():
    raw = (
        b"Message-ID: <c@x>\r\nFrom: a@x.test\r\nTo: b@x.test\r\nSubject: s\r\n"
        b"References: <a@x>\r\n <b@x>\r\n"
        b"\r\nbody\r\n"
    )
    assert parse_mime(raw).references == ["<a@x>", "<b@x>"]


def test_latin1_and_rfc2047():
    p = parse_mime(h.LATIN1)
    assert p.subject == "Café invoice"
    assert p.body_text is not None and "café" in p.body_text


def test_unknown_charset_degrades():
    p = parse_mime(h.BAD_CHARSET)
    assert p.body_text is not None and "hello" in p.body_text


def test_alternative_and_attachment_and_inline():
    alt = h.multipart_part(
        [h.text_part("plain body"), h.text_part("<b>html</b>", "html")], "alternative", "ALT"
    )
    raw = h.multipart(
        [
            alt,
            h.attachment_part("../../etc/passwd.pdf", "application/pdf", h.PDF),
            h.attachment_part("logo.png", "image/png", h.PNG, "inline", "<logo@x>"),
        ]
    )
    p = parse_mime(raw)
    assert p.body_text == "plain body"
    assert p.body_html == "<b>html</b>"
    assert [a.filename for a in p.attachments] == ["passwd.pdf", "logo.png"]
    assert p.attachments[0].content == h.PDF
    assert p.attachments[0].checksum
    assert p.attachments[1].inline and p.attachments[1].content_id == "<logo@x>"


def test_first_body_wins_and_text_attachment():
    raw = h.multipart(
        [h.text_part("one"), h.text_part("two"), h.attachment_part("n.txt", "text/plain", b"hi")]
    )
    p = parse_mime(raw)
    assert p.body_text == "one"
    assert p.attachments[0].filename == "n.txt"


def test_nested_rfc822_is_an_attachment_not_flattened():
    inner = h.PLAIN
    part = (
        b"Content-Type: message/rfc822\r\nContent-Disposition: attachment; filename=fwd.eml\r\n\r\n"
        + inner
    )
    p = parse_mime(h.multipart([h.text_part("see attached"), part]))
    assert p.body_text == "see attached"
    assert p.attachments[0].mime_type == "message/rfc822"
    assert b"Invoice INV-001" in p.attachments[0].content


def test_nested_rfc822_without_filename():
    part = b"Content-Type: message/rfc822\r\n\r\n" + h.PLAIN
    p = parse_mime(h.multipart([part]))
    assert p.attachments[0].filename == "message.eml"


def test_warnings_for_missing_fields():
    p = parse_mime(b"Subject: hi\r\n\r\nbody\r\n")
    assert p.message_id is None and p.from_ is None
    assert "missing Message-ID" in p.warnings and "missing From" in p.warnings


def test_bad_and_naive_dates():
    bad = parse_mime(b"Message-ID: <a@b>\r\nFrom: a@b.test\r\nDate: not a date\r\n\r\nx\r\n")
    assert bad.date is None and "unparseable Date" in bad.warnings
    naive = parse_mime(
        b"Message-ID: <a@b>\r\nFrom: a@b.test\r\nDate: Thu, 15 Oct 2026 09:00:00 -0000\r\n\r\nx\r\n"
    )
    assert naive.date is not None and naive.date.tzinfo is not None


def test_naive_date_without_tz_is_utc():
    from unittest.mock import patch

    naive = datetime(2026, 10, 15, 9, 0)
    with patch("mailsim.mime.parse.parsedate_to_datetime", return_value=naive):
        p = parse_mime(b"Message-ID: <a@b>\r\nFrom: a@b.test\r\nDate: x\r\n\r\nx\r\n")
    assert p.date == naive.replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("name", "ctype"),
    [("run.exe", "application/octet-stream"), ("a.txt", "application/x-msdownload")],
)
def test_denied_types(name, ctype):
    raw = h.multipart([h.text_part("x"), h.attachment_part(name, ctype, b"MZ")])
    with pytest.raises(InvalidRequest):
        parse_mime(raw)


def test_size_and_count_limits():
    big = h.multipart(
        [h.text_part("x"), h.attachment_part("a.bin", "application/octet-stream", b"0" * 100)]
    )
    with pytest.raises(InvalidRequest, match="too large"):
        parse_mime(big, MimeLimits(max_attachment_bytes=10))
    with pytest.raises(InvalidRequest, match="message too large"):
        parse_mime(big, MimeLimits(max_message_bytes=10))
    two = h.multipart(
        [h.attachment_part(f"{i}.bin", "application/octet-stream", b"1") for i in range(3)]
    )
    with pytest.raises(InvalidRequest, match="too many"):
        parse_mime(two, MimeLimits(max_attachments=2))
    with pytest.raises(InvalidRequest, match="empty"):
        parse_mime(b"  \r\n")


def test_zero_byte_attachment_ok():
    p = parse_mime(
        h.multipart([h.text_part("x"), h.attachment_part("z.bin", "application/octet-stream", b"")])
    )
    assert p.attachments[0].content == b"" and p.attachments[0].filename == "z.bin"


def test_attachment_without_payload_bytes():
    from email.message import EmailMessage

    from mailsim.mime.parse import ParsedMessage, _add_attachment

    out = ParsedMessage(None, None, [], None, [], [], [], "", None, None, None, [])
    _add_attachment(out, MimeLimits(), None, "application/x-thing", b"", EmailMessage(), "")
    assert out.attachments[0].filename == "attachment"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "attachment"),
        ("", "attachment"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Windows\\evil.txt", "evil.txt"),
        ("a\x00b\x1f.txt", "ab.txt"),
        ("...", "attachment"),
        ("x" * 400 + ".txt", ("x" * 400 + ".txt")[:255]),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def _build(**kw):
    base = dict(
        message_id="<m1@example.test>",
        from_=Address(email="a@example.test", name="A"),
        to=[Address(email="b@example.test")],
        subject="Hello",
        date=datetime(2026, 10, 15, 9, tzinfo=UTC),
        text="hi",
    )
    base.update(kw)
    return build_mime(**base)


def test_build_parse_roundtrip_with_attachments_and_html():
    att = ParsedAttachment("a.pdf", "application/pdf", h.PDF, False, None)
    inline = ParsedAttachment("l.png", "image/png", h.PNG, True, "<l@x>")
    raw = _build(
        html="<p>hi</p>",
        cc=[Address(email="c@example.test")],
        in_reply_to="<p@x>",
        references=["<p@x>"],
        attachments=[att, inline],
    )
    p = parse_mime(raw)
    assert p.message_id == "<m1@example.test>"
    assert p.in_reply_to == "<p@x>" and p.references == ["<p@x>"]
    assert p.body_text == "hi\n" and p.body_html is not None and "<p>hi</p>" in p.body_html
    assert [a.checksum for a in p.attachments] == [att.checksum, inline.checksum]
    assert p.attachments[1].inline and p.attachments[1].content_id == "<l@x>"
    assert p.cc[0].email == "c@example.test"


def test_build_is_deterministic_and_never_writes_bcc():
    att = ParsedAttachment("a.pdf", "application/pdf", h.PDF, False, None)
    assert _build(attachments=[att]) == _build(attachments=[att])
    assert b"Bcc" not in _build()
    assert b"To:" not in _build(to=[])
    assert b"A <a@example.test>" in _build()


def test_build_writes_the_date_in_utc_whatever_the_input_offset():
    # issue #6: a timestamptz read back in a non-UTC session must not change the bytes
    ist = datetime(2026, 10, 15, 14, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert b"Date: Thu, 15 Oct 2026 09:00:00 +0000" in _build(date=ist)
    assert _build(date=ist) == _build()
    # a naive value keeps the stdlib's "unknown zone" form (never the host's local zone)
    assert b"Date: Thu, 15 Oct 2026 09:00:00 -0000" in _build(date=datetime(2026, 10, 15, 9))


def test_deep_nesting_is_rejected_cleanly():
    part = h.text_part("x")
    for i in range(30):
        part = h.multipart_part([part], "mixed", f"B{i}")
    raw = b"Message-ID: <d@x>\r\nFrom: a@x.test\r\nMIME-Version: 1.0\r\n" + part + b"\r\n"
    with pytest.raises(InvalidRequest, match="nested"):
        parse_mime(raw)


def test_extreme_nesting_recursion_error_is_typed():
    from unittest.mock import patch

    with (
        patch("mailsim.mime.parse.BytesParser.parsebytes", side_effect=RecursionError),
        pytest.raises(InvalidRequest, match="nested"),
    ):
        parse_mime(b"From: a@x.test\r\n\r\nx")
    msg = parse_mime(h.PLAIN)
    with (
        patch("mailsim.mime.parse._walk", side_effect=RecursionError),
        pytest.raises(InvalidRequest),
    ):
        parse_mime(h.PLAIN)
    assert msg


def test_trailing_dot_space_cannot_hide_an_extension():
    assert sanitize_filename("a.exe .") == "a.exe"
    raw = h.multipart(
        [h.text_part("x"), h.attachment_part("evil.exe .", "application/octet-stream", b"MZ")]
    )
    with pytest.raises(InvalidRequest):
        parse_mime(raw)
