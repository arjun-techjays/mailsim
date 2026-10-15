"""IMAP4rev1 wire syntax (RFC 3501 section 9): command tokenizer, sequence sets, and response
string rendering. Pure functions over bytes; no I/O.

Token model:
- `str`      an atom (NIL is the atom "NIL"); a bracketed fetch section such as
             `BODY.PEEK[HEADER.FIELDS (FROM TO)]<0.100>` is ONE atom
- `QStr`     a quoted string (escapes resolved)
- `bytes`    a literal `{n}` / `{n+}` (contents verbatim)
- `list`     a parenthesized list
"""

from __future__ import annotations

import re
from collections.abc import Iterable


class ParseError(ValueError):
    """Malformed command syntax (answered with BAD)."""


class QStr(str):
    """A quoted string (distinct from an atom)."""


LITERAL_RE = re.compile(rb"\{(\d{1,10})(\+?)\}\r?\n$")
_ATOM_SPECIALS = b'(){ "\r\n'  # `*`, `%` and `\\` are legal in sets, patterns and flags
_TAG_RE = re.compile(r"^[!#$&',\-./0-9:;<=>?@A-Z^_`a-z|~\[]+$")  # ASTRING-CHAR minus '+'


def literal_marker(line: bytes) -> tuple[int, bool] | None:
    """`(size, non_synchronizing)` when a command line ends with a literal marker."""
    found = LITERAL_RE.search(line)
    if found is None:
        return None
    return int(found.group(1)), bool(found.group(2))


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def peek(self) -> int | None:
        return self.data[self.pos] if self.pos < len(self.data) else None

    def eof(self) -> bool:
        return self.pos >= len(self.data) or self.data[self.pos :] in (b"\r\n", b"\n")


MAX_DEPTH = 32  # parenthesized list nesting
_SECTION_PREFIX = re.compile(rb"(?:BODY|BODY\.PEEK|BINARY|BINARY\.PEEK|BINARY\.SIZE)$", re.I)


def tokenize(data: bytes) -> list[object]:
    """Tokenize a complete command (literals inline, as received). Raises ParseError."""
    r = _Reader(data)
    out = _items(r, nested=False)
    if not r.eof():  # pragma: no cover - _items consumes to EOF or raises
        raise ParseError("trailing data")
    return out


def _items(r: _Reader, *, nested: bool, depth: int = 0) -> list[object]:
    items: list[object] = []
    first = True
    while True:
        if r.eof():
            if nested:
                raise ParseError("unterminated list")
            return items
        c = r.peek()
        if c == ord(")"):
            if not nested:
                raise ParseError("unexpected ')'")
            r.pos += 1
            return items
        if not first:
            if c != ord(" "):
                raise ParseError("expected SP between arguments")
            r.pos += 1
            if nested and r.peek() == ord(")"):
                raise ParseError("trailing SP in list")
        first = False
        items.append(_item(r, depth=depth))


def _item(r: _Reader, *, depth: int) -> object:
    c = r.peek()
    if c is None or c in (ord("\r"), ord("\n")):
        raise ParseError("missing argument")
    if c == ord("("):
        if depth >= MAX_DEPTH:
            raise ParseError("lists nested too deeply")
        r.pos += 1
        if r.peek() == ord(")"):
            r.pos += 1
            return []
        return _items(r, nested=True, depth=depth + 1)
    if c == ord('"'):
        return _quoted(r)
    if c == ord("{"):
        return _literal(r)
    return _atom(r)


def _quoted(r: _Reader) -> QStr:
    r.pos += 1
    buf = bytearray()
    while True:
        c = r.peek()
        if c is None or c in (ord("\r"), ord("\n")):
            raise ParseError("unterminated quoted string")
        r.pos += 1
        if c == ord('"'):
            return QStr(buf.decode("utf-8", "replace"))
        if c == ord("\\"):
            nxt = r.peek()
            if nxt not in (ord('"'), ord("\\")):
                raise ParseError("invalid escape in quoted string")
            r.pos += 1
            buf.append(nxt)
            continue
        buf.append(c)


def _literal(r: _Reader) -> bytes:
    m = re.compile(rb"\{(\d{1,10})\+?\}\r?\n").match(r.data, r.pos)
    if m is None:
        raise ParseError("malformed literal")
    size = int(m.group(1))
    start = m.end()
    if start + size > len(r.data):
        raise ParseError("literal shorter than announced")
    r.pos = start + size
    return r.data[start : start + size]


def _atom(r: _Reader) -> str:
    start = r.pos
    depth = 0  # inside a fetch section [...] spaces and parentheses belong to the atom
    while r.pos < len(r.data):
        c = r.data[r.pos]
        if c == ord("[") and (depth or _SECTION_PREFIX.search(r.data[start : r.pos])):
            depth += 1
        elif c == ord("]") and depth:
            depth -= 1
        elif c in (ord("\r"), ord("\n")) or depth == 0 and c in _ATOM_SPECIALS:
            break
        r.pos += 1
    if depth:
        raise ParseError("unterminated '['")
    if r.pos == start:
        raise ParseError(f"unexpected character {chr(r.data[start])!r}")
    return r.data[start : r.pos].decode("utf-8", "replace")


def valid_tag(tag: object) -> bool:
    return isinstance(tag, str) and not isinstance(tag, QStr) and bool(_TAG_RE.match(tag))


def astring(tok: object) -> str:
    """atom / quoted / literal -> str (mailbox names, credentials, search strings)."""
    if isinstance(tok, bytes):
        return tok.decode("utf-8", "replace")
    if isinstance(tok, str):
        return str(tok)
    raise ParseError("expected a string")


# ------------------------------------------------------------------------- sequence sets
_SEQ_RE = re.compile(r"^(\d{1,10}|\*)(?::(\d{1,10}|\*))?$")


def parse_sequence_set(text: object, largest: int) -> list[tuple[int, int]]:
    """`1:3,5,7:*` -> inclusive (low, high) ranges. `*` = `largest` (0 when empty)."""
    if not isinstance(text, str) or isinstance(text, QStr) or not text:
        raise ParseError("invalid sequence set")
    ranges: list[tuple[int, int]] = []
    for piece in text.split(","):
        m = _SEQ_RE.match(piece)
        if m is None:
            raise ParseError(f"invalid sequence set {text!r}")
        a = largest if m.group(1) == "*" else int(m.group(1))
        b = a if m.group(2) is None else (largest if m.group(2) == "*" else int(m.group(2)))
        if (m.group(1) != "*" and a == 0) or (m.group(2) not in (None, "*") and b == 0):
            raise ParseError("0 is not a valid message number")
        ranges.append((min(a, b), max(a, b)))
    return ranges


def in_ranges(n: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(lo <= n <= hi for lo, hi in ranges)


def is_sequence_set(tok: object) -> bool:
    if not isinstance(tok, str) or isinstance(tok, QStr):
        return False
    try:
        parse_sequence_set(tok, 1)
    except ParseError:
        return False
    return True


# ------------------------------------------------------------------------- rendering
_SAFE_QUOTED = re.compile(rb"^[\x01-\x09\x0b\x0c\x0e-\x7f]*$")


def nstring(value: str | bytes | None) -> bytes:
    """NIL, a quoted string, or a literal when the value needs one."""
    if value is None:
        return b"NIL"
    return string(value)


def string(value: str | bytes) -> bytes:
    raw = value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value
    if _SAFE_QUOTED.match(raw) and len(raw) < 1024:
        return b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'
    return literal(raw)


def literal(raw: bytes) -> bytes:
    return b"{%d}\r\n" % len(raw) + raw


def compress_ranges(numbers: Iterable[int]) -> str:
    """[1, 2, 3, 7] -> "1:3,7" (UIDPLUS uid-set)."""
    nums = sorted(set(numbers))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}:{nums[j]}")
        i = j + 1
    return ",".join(parts)


__all__ = [
    "ParseError",
    "QStr",
    "astring",
    "compress_ranges",
    "in_ranges",
    "is_sequence_set",
    "literal",
    "literal_marker",
    "nstring",
    "parse_sequence_set",
    "string",
    "tokenize",
    "valid_tag",
]
