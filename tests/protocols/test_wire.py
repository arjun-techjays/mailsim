"""Pure unit tests: IMAP tokenizer, sequence sets, string rendering, MIME section views."""

from __future__ import annotations

import pytest

from mailsim.protocols import imapmime as mm
from mailsim.protocols.imapwire import (
    ParseError,
    QStr,
    astring,
    compress_ranges,
    is_sequence_set,
    literal_marker,
    nstring,
    parse_sequence_set,
    string,
    tokenize,
    valid_tag,
)


def test_tokenize_shapes():
    toks = tokenize(
        b'a1 FETCH 1:* (FLAGS BODY.PEEK[HEADER.FIELDS (A B)]<0.5>) "q\\"s" {3}\r\nabc\r\n'
    )
    assert toks == ["a1", "FETCH", "1:*", ["FLAGS", "BODY.PEEK[HEADER.FIELDS (A B)]<0.5>"],
                    'q"s', b"abc"]  # fmt: skip
    assert isinstance(toks[4], QStr)
    assert tokenize(b"a () (())") == ["a", [], [[]]]
    assert tokenize(b"a \\Seen $kw") == ["a", "\\Seen", "$kw"]
    with pytest.raises(ParseError, match="shorter than announced"):
        tokenize(b"a {10}\r\nabc")
    with pytest.raises(ParseError, match="missing argument"):
        tokenize(b"a ")


def test_helpers():
    assert literal_marker(b"a LOGIN {12}\r\n") == (12, False)
    assert literal_marker(b"a LOGIN {12+}\r\n") == (12, True)
    assert literal_marker(b"a NOOP\r\n") is None
    assert valid_tag("A001") and not valid_tag(QStr("x")) and not valid_tag("a+b")
    assert astring(b"bytes") == "bytes" and astring(QStr("q")) == "q"
    with pytest.raises(ParseError):
        astring(["list"])
    assert compress_ranges([5, 1, 2, 3, 7, 8]) == "1:3,5,7:8"
    assert compress_ranges([]) == ""


def test_sequence_sets():
    assert parse_sequence_set("1:3,5,7:*", 9) == [(1, 3), (5, 5), (7, 9)]
    assert parse_sequence_set("*:4", 2) == [(2, 4)]
    assert parse_sequence_set("*", 0) == [(0, 0)]
    for bad in ("", "a", "1:", "0", "1:0", QStr("1"), 5):
        with pytest.raises(ParseError):
            parse_sequence_set(bad, 3)
    assert is_sequence_set("2,4:*") and not is_sequence_set("ALL") and not is_sequence_set(b"1")


def test_string_rendering():
    assert nstring(None) == b"NIL"
    assert string('a"b\\c') == b'"a\\"b\\\\c"'
    assert string("line\r\nbreak") == b"{11}\r\nline\r\nbreak"
    assert string("x" * 2000).startswith(b"{2000}\r\n")
    assert string(b"\xff") == b"{1}\r\n\xff"


NESTED = (
    b"From: a@example.test\r\nSubject: outer\r\nContent-Type: multipart/mixed; boundary=o\r\n"
    b"\r\npreamble\r\n--o\r\nContent-Type: text/plain\r\n\r\nfirst\r\n--o\r\n"
    b"Content-Type: message/rfc822\r\nContent-Description: fwd\r\n\r\n"
    b"From: b@example.test\r\nSubject: inner\r\nContent-Type: multipart/alternative; "
    b"boundary=i\r\n\r\n--i\r\nContent-Type: text/plain\r\n\r\ninner text\r\n--i\r\n"
    b"Content-Type: text/html; charset*=utf-8''%E2%82%AC\r\nContent-Language: en\r\n\r\n"
    b"<p>x</p>\r\n--i--\r\n\r\n--o--\r\nepilogue\r\n"
)


def test_nested_sections():
    root = mm.parse(NESTED)
    get = lambda spec, fields=None: mm.section_bytes(NESTED, root, mm.parse_section(spec, fields))  # noqa: E731
    assert get("1") == b"first"
    assert get("2").startswith(b"From: b@example.test")
    assert get("2.HEADER").endswith(b"boundary=i\r\n\r\n")
    assert get("2.TEXT").startswith(b"--i")
    assert get("2.1") == b"inner text"
    assert get("2.2") == b"<p>x</p>"
    assert get("2.2.MIME").startswith(b"Content-Type: text/html")
    assert get("2.HEADER.FIELDS", ["SUBJECT"]) == b"Subject: inner\r\n\r\n"
    assert get("2.MIME").startswith(b"Content-Type: message/rfc822")
    assert get("HEADER.FIELDS.NOT", ["CONTENT-TYPE", "FROM"]) == b"Subject: outer\r\n\r\n"
    for spec in ("3", "1.1", "2.3", "1.HEADER", "2.1.1"):
        with pytest.raises(mm.SectionError):
            get(spec)
    for spec in ("1.", ".1", "1HEADER", "X"):
        with pytest.raises(mm.SectionError):
            mm.parse_section(spec)
    structure = mm.bodystructure(root)
    assert b'"MESSAGE" "RFC822" NIL NIL "fwd" "7BIT"' in structure
    assert b'("CHARSET" "\xe2\x82\xac")' in structure or b'("CHARSET" {3}' in structure
    assert b'"ALTERNATIVE" ("BOUNDARY" "i") NIL "en"' not in structure
    assert b' "en" NIL)' in structure
    basic = mm.bodystructure(root, extended=False)
    assert basic.endswith(b' "MIXED")')


def test_single_part_and_odd_messages():
    single = b"Subject: s\r\n\r\nbody\r\n"
    root = mm.parse(single)
    assert mm.section_bytes(single, root, mm.parse_section("1")) == b"body\r\n"
    assert mm.section_bytes(single, root, mm.parse_section("1.MIME")) == b"Subject: s\r\n\r\n"
    assert mm.bodystructure(root) == (b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 6 1 NIL NIL NIL NIL)')
    headerless = b"\r\nonly body"
    assert mm.split_header(headerless) == (b"\r\n", b"only body")
    assert mm.split_header(b"\nx") == (b"\n", b"x")
    assert mm.split_header(b"Subject: no body") == (b"Subject: no body", b"")
    broken = b"Content-Type: multipart/mixed\r\n\r\nno boundary"
    assert b'"TEXT" "PLAIN"' in mm.bodystructure(mm.parse(broken))
    unterminated = b"Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n\r\nlast"
    assert mm.parse(unterminated).children[0].body == b"last"
    assert mm.split_multipart(b"no delimiters", "b") == []
    deep = b"Content-Type: message/rfc822\r\n\r\n" * 30 + b"x"
    assert mm.parse(deep) is not None
    env = mm.envelope(mm.parse(b"From: undisclosed-recipients:;\r\nTo: plain\r\n\r\n"))
    assert env.startswith(b'(NIL NIL NIL NIL NIL ((NIL NIL "plain" NIL)) NIL NIL NIL NIL)')


def test_header_fields_without_blank_line():
    assert mm.header_fields(b"A: 1\r\n folded\r\nB: 2", ["a"], exclude=False) == (
        b"A: 1\r\n folded\r\n\r\n"
    )
