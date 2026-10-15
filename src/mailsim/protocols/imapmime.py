"""Byte-exact MIME views of a raw message for IMAP FETCH (RFC 3501 6.4.5 / 7.4.2).

The raw message is split by offsets (never re-serialized) so `BODY[...]` sections and
partial fetches return exactly the stored octets. Header values come from the stdlib parser
in `compat32` mode, which keeps encoded-words and folding intact as IMAP requires for
ENVELOPE. Nothing is decoded, executed or rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import collapse_rfc2231_value, getaddresses

from mailsim.protocols.imapwire import nstring, string

_SEP = re.compile(rb"\r?\n\r?\n")
_UNFOLD = re.compile(r"\r?\n(?=[ \t])")
MAX_DEPTH = 20


class SectionError(ValueError):
    """A FETCH section that does not exist in this message (answered with BAD)."""


@dataclass
class Part:
    header: bytes  # including the terminating blank line (empty when the part has none)
    body: bytes
    headers: Message
    children: list[Part] = field(default_factory=list)  # multipart/*
    message: Part | None = None  # message/rfc822 encapsulated message
    fields: dict[str, list[str]] = field(default_factory=dict)  # raw, unfolded header values

    @property
    def maintype(self) -> str:
        return self.headers.get_content_maintype()

    @property
    def subtype(self) -> str:
        return self.headers.get_content_subtype()

    @property
    def is_multipart(self) -> bool:
        return self.maintype == "multipart"


def _headers(raw_header: bytes) -> Message:
    return BytesParser(policy=policy.compat32).parsebytes(raw_header, headersonly=True)


def split_header(raw: bytes) -> tuple[bytes, bytes]:
    if raw.startswith(b"\r\n") or raw.startswith(b"\n"):  # no header at all
        nl = 2 if raw.startswith(b"\r\n") else 1
        return raw[:nl], raw[nl:]
    m = _SEP.search(raw)
    if m is None:
        return raw, b""
    return raw[: m.end()], raw[m.end() :]


def split_multipart(body: bytes, boundary: str) -> list[bytes]:
    b = re.escape(boundary.encode("utf-8", "surrogateescape"))
    delim = re.compile(rb"(?:^|\r?\n)--" + b + rb"(--)?[ \t]*(?:\r?\n|$)")
    parts: list[bytes] = []
    pos: int | None = None
    for m in delim.finditer(body):
        if pos is not None:
            parts.append(body[pos : m.start()])
        if m.group(1):
            return parts
        pos = m.end()
    if pos is not None:  # unterminated multipart: keep what is there
        parts.append(body[pos:])
    return parts


def parse(raw: bytes, depth: int = 0) -> Part:
    header, body = split_header(raw)
    headers = _headers(header)
    part = Part(header, body, headers, fields=raw_fields(header))
    if depth >= MAX_DEPTH:
        return part
    ctype = headers.get_content_type()
    if part.is_multipart:
        boundary = headers.get_param("boundary")
        if isinstance(boundary, str) and boundary:
            part.children = [parse(p, depth + 1) for p in split_multipart(body, boundary)]
    elif ctype == "message/rfc822":
        part.message = parse(body, depth + 1)
    return part


# ------------------------------------------------------------------------- header fields
def raw_fields(header: bytes) -> dict[str, list[str]]:
    """Header values exactly as sent (encoded-words kept, 8-bit bytes surrogate-escaped so
    they render back verbatim), unfolded, keyed by lower-cased field name."""
    text = _UNFOLD.sub("", header.decode("utf-8", "surrogateescape"))
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() and " " not in name.strip():
            out.setdefault(name.strip().lower(), []).append(value.strip())
    return out


def header_value(part: Part, name: str) -> str | None:
    values = part.fields.get(name.lower())
    return values[0] if values else None


def header_fields(header: bytes, names: list[str], *, exclude: bool) -> bytes:
    """HEADER.FIELDS / HEADER.FIELDS.NOT: matching header lines (with continuations), in
    original order, followed by the blank line."""
    wanted = {n.lower() for n in names}
    out = bytearray()
    keep = False
    for line in header.splitlines(keepends=True):
        if line in (b"\r\n", b"\n"):
            break
        if line[:1] in (b" ", b"\t"):
            if keep:
                out += line
            continue
        name = line.split(b":", 1)[0].strip().decode("ascii", "replace").lower()
        keep = (name in wanted) != exclude
        if keep:
            out += line
    return bytes(out) + b"\r\n"


# ------------------------------------------------------------------------- ENVELOPE
def _addresses(part: Part, name: str) -> bytes:
    values = part.fields.get(name.lower(), [])
    if not values:
        return b"NIL"
    pairs = getaddresses(values)
    items = []
    for display, addr in pairs:
        if not addr:
            continue
        local, _, host = addr.rpartition("@") if "@" in addr else (addr, "", "")
        items.append(b"(" + nstring(display or None) + b" NIL " + nstring(local) + b" "
                     + nstring(host or None) + b")")  # fmt: skip
    return b"(" + b"".join(items) + b")" if items else b"NIL"


def envelope(part: Part) -> bytes:
    sender = "Sender" if header_value(part, "Sender") else "From"
    reply_to = "Reply-To" if header_value(part, "Reply-To") else "From"
    fields = [
        nstring(header_value(part, "Date")),
        nstring(header_value(part, "Subject")),
        _addresses(part, "From"),
        _addresses(part, sender),
        _addresses(part, reply_to),
        _addresses(part, "To"),
        _addresses(part, "Cc"),
        _addresses(part, "Bcc"),
        nstring(header_value(part, "In-Reply-To")),
        nstring(header_value(part, "Message-ID")),
    ]
    return b"(" + b" ".join(fields) + b")"


# ------------------------------------------------------------------------- BODYSTRUCTURE
def _params(headers: Message, name: str = "content-type") -> bytes:
    params = headers.get_params(header=name)
    pairs = (params or [])[1:]
    if not pairs:
        return b"NIL"
    out = [string(k.upper()) + b" " + string(collapse_rfc2231_value(v)) for k, v in pairs]
    return b"(" + b" ".join(out) + b")"


def _disposition(headers: Message) -> bytes:
    raw = headers.get("Content-Disposition")
    if not raw:
        return b"NIL"
    kind = str(raw).split(";", 1)[0].strip()
    return b"(" + string(kind.upper()) + b" " + _params(headers, "content-disposition") + b")"


def _lines(body: bytes) -> int:
    return body.count(b"\n") + (1 if body and not body.endswith(b"\n") else 0)


def bodystructure(part: Part, *, extended: bool = True) -> bytes:
    h = part.headers
    if part.is_multipart and part.children:
        inner = b"".join(bodystructure(c, extended=extended) for c in part.children)
        out = inner + b" " + string(part.subtype.upper())
        if extended:
            out += b" " + _params(h) + b" " + _disposition(h) + b" "
            out += nstring(header_value(part, "Content-Language")) + b" "
            out += nstring(header_value(part, "Content-Location"))
        return b"(" + out + b")"
    maintype, subtype = h.get_content_maintype(), h.get_content_subtype()
    if part.is_multipart:  # multipart without usable parts: describe as text/plain
        maintype, subtype = "text", "plain"
    fields = [
        string(maintype.upper()),
        string(subtype.upper()),
        _params(h),
        nstring(header_value(part, "Content-ID")),
        nstring(header_value(part, "Content-Description")),
        string((header_value(part, "Content-Transfer-Encoding") or "7BIT").upper()),
        str(len(part.body)).encode(),
    ]
    if part.message is not None:
        fields += [envelope(part.message),
                   bodystructure(part.message, extended=extended),
                   str(_lines(part.body)).encode()]  # fmt: skip
    elif maintype == "text":
        fields.append(str(_lines(part.body)).encode())
    if extended:
        fields += [
            nstring(header_value(part, "Content-MD5")),
            _disposition(h),
            nstring(header_value(part, "Content-Language")),
            nstring(header_value(part, "Content-Location")),
        ]
    return b"(" + b" ".join(fields) + b")"


# ------------------------------------------------------------------------- sections
_SECTION_RE = re.compile(
    r"^((?:\d{1,10}\.)*\d{1,10})?(?:\.?(HEADER\.FIELDS\.NOT|HEADER\.FIELDS|HEADER|TEXT|MIME))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Section:
    parts: tuple[int, ...]
    text: str  # "", HEADER, TEXT, MIME, HEADER.FIELDS, HEADER.FIELDS.NOT
    fields: tuple[str, ...] = ()


def parse_section(spec: str, fields: list[str] | None = None) -> Section:
    m = _SECTION_RE.match(spec)
    if m is None or spec.startswith(".") or spec.endswith("."):
        raise SectionError(f"invalid section {spec!r}")
    nums = tuple(int(n) for n in m.group(1).split(".")) if m.group(1) else ()
    text = (m.group(2) or "").upper()
    if m.group(1) and m.group(2) and not spec[len(m.group(1)) :].startswith("."):
        raise SectionError(f"invalid section {spec!r}")
    if any(n == 0 for n in nums):
        raise SectionError("part numbers start at 1")
    if text == "MIME" and not nums:
        raise SectionError("MIME requires a part number")
    if text.startswith("HEADER.FIELDS") != (fields is not None):
        raise SectionError("HEADER.FIELDS requires a field list")
    return Section(nums, text, tuple(fields or ()))


def _locate(root: Part, nums: tuple[int, ...]) -> Part:
    node, is_message = root, True
    for n in nums:
        if not is_message and node.message is not None:
            node, is_message = node.message, True
        if node.is_multipart and node.children:
            if n > len(node.children):
                raise SectionError("no such body part")
            node = node.children[n - 1]
        elif n != 1 or not is_message:
            raise SectionError("no such body part")
        is_message = False
    return node


def section_bytes(raw: bytes, root: Part, section: Section) -> bytes:
    if not section.parts:
        target = root
        if section.text == "":
            return raw
    else:
        node = _locate(root, section.parts)
        if section.text == "":
            return node.body
        if section.text == "MIME":
            return node.header if node is not root else root.header
        if node.message is None:
            raise SectionError(f"{section.text} needs a message/rfc822 part")
        target = node.message
    if section.text == "HEADER":
        return target.header
    if section.text == "TEXT":
        return target.body
    return header_fields(target.header, list(section.fields),
                         exclude=section.text == "HEADER.FIELDS.NOT")  # fmt: skip


__all__ = [
    "Part",
    "Section",
    "SectionError",
    "bodystructure",
    "envelope",
    "header_fields",
    "header_value",
    "parse",
    "parse_section",
    "section_bytes",
    "split_header",
    "split_multipart",
]
