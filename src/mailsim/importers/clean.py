"""Body cleaning and quoted-chain splitting for raw corpus messages (bodies without headers).

`split_chain` finds the quoted/forwarded header blocks that Outlook ("-----Original
Message-----", From:/Sent:/To:/Subject:) and Lotus Notes ("Forwarded by ...", "Name\\n date",
"x on 01/02/2001 ...", To:/cc:/Subject:) leave inside a body, and returns the segments newest
first. Each quoted segment carries the raw header fields it was introduced with.
`clean_body` normalises charset and transfer-encoding artefacts and whitespace, and can drop
'>' quoted lines and trailing legal disclaimers.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_SUBJECT = re.compile(r"^\s*Subject\s*:\s?(.*)$", re.IGNORECASE)
_FIELD = re.compile(
    r"^\s*(From|To|Cc|Bcc|Sent|Date|Sent by|Importance|Reply-To)\s*:\s*(.*)$", re.IGNORECASE
)
_FWD = re.compile(r"(-{3,}|^\s*)\s*(Forwarded by|Original Message|Forwarded message)", re.I)
_FWD_BY = re.compile(r"Forwarded by\s+(.+?)\s+on\s+\d", re.IGNORECASE)
_DASHES = re.compile(r"^[\s\d:/APM]*-{5,}\s*$")
_ON_DATE = re.compile(
    r"\bon \d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)?\s*$", re.IGNORECASE
)
_DATE_LINE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M\s*$")
_FROM_LOTUS = re.compile(r"^\s*From:\s+(.+?)\s{2,}(\d{1,2}/\d{1,2}/\d{2,4}.*)$")
_ORG_LINE = re.compile(r"^\s*(Enron|Corvane)[\w\s.&,]{0,40}$")
_NAME_LINE = re.compile(r"^\s*[A-Z][\w'.-]*(?:\s+[A-Z@][\w'.@-]*){0,3}\s*$")
_CONTINUATION = re.compile(r"(@|/[A-Z]{2,}|[,;]\s*$)")
_DISCLAIMER = re.compile(
    r"(intended recipient|confidential(ity)? notice|privileged|disclaimer|"
    r"this e-?mail (message )?(is|and any)|not the intended)",
    re.IGNORECASE,
)
_RULE = re.compile(r"^\s*[-*_=~]{20,}\s*$")
_SMART_APOS = re.compile(r"(?<=[A-Za-z])\?(?=(?:s|t|ll|re|ve|d|m)\b)")
_SMART_DASH = re.compile(r"(?<=\w) \? (?=\w)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LONG_TOKEN = re.compile(r"\S{60,}")
MAX_SEGMENTS = 8


@dataclass
class QuotedHeaders:
    """Raw header fields of a quoted/forwarded message (as written inside the body)."""

    from_: str | None = None
    to: str = ""
    cc: str = ""
    subject: str | None = None
    date: str | None = None
    kind: str = "reply"  # "reply" | "forward" (how the NEWER segment relates to this one)
    forwarded_by: str | None = None  # who forwarded it ("Forwarded by X on ...")


@dataclass
class Segment:
    body: str
    headers: QuotedHeaders | None = None  # None for the newest (top) segment


def _next_nonblank(lines: list[str], j: int) -> str:
    for k in range(j + 1, min(len(lines), j + 4)):
        if lines[k].strip():
            return lines[k]
    return ""


def _headerish(lines: list[str], j: int) -> bool:
    line = lines[j]
    if _FIELD.match(line) or _FWD.search(line) or _DASHES.match(line):
        return True
    if _ON_DATE.search(line) or _DATE_LINE.match(line) or _ORG_LINE.match(line):
        return True
    return bool(
        len(line.strip()) <= 60
        and _NAME_LINE.match(line)
        and _DATE_LINE.match(_next_nonblank(lines, j))
    )


def _block_start(lines: list[str], s: int, floor: int) -> int | None:
    """First line of the header block that ends with the Subject line at `s`, or None."""
    start, blanks, j, fields = s, 0, s - 1, False
    while j >= floor and s - j <= 18:
        line = lines[j]
        if not line.strip():
            blanks += 1
            if blanks > 6:
                break
        elif _headerish(lines, j):
            start, blanks = j, 0
            fields = True
        elif _CONTINUATION.search(line) and len(line) < 200:
            start, blanks = j, 0
        else:
            break
        j -= 1
    if not fields:
        return None
    # a separator ("--- Forwarded by" wrapped over two lines) right above belongs to the block
    while start - 1 >= floor and (_FWD.search(lines[start - 1]) or _DASHES.match(lines[start - 1])):
        start -= 1
    return start


def _parse_block(lines: list[str]) -> QuotedHeaders:
    h = QuotedHeaders()
    current: str | None = None
    values: dict[str, list[str]] = {}
    for idx, line in enumerate(lines):
        if _FWD.search(line) and "forward" in line.lower():
            h.kind = "forward"
            by = _FWD_BY.search(line)
            if by:
                h.forwarded_by = by.group(1).strip()
            current = None
            continue
        lotus = _FROM_LOTUS.match(line)
        if lotus:
            h.from_, h.date = lotus.group(1).strip(), lotus.group(2).strip()
            current = None
            continue
        m = _FIELD.match(line)
        sm = _SUBJECT.match(line)
        if sm:
            h.subject = sm.group(1).strip()
            current = None
            continue
        if m:
            current = m.group(1).lower()
            values.setdefault(current, []).append(m.group(2).strip())
            continue
        on = _ON_DATE.search(line)
        if on and not _FWD.search(line):
            h.from_ = line[: on.start()].strip()
            h.date = line[on.start() + 3 :].strip()
            current = None
            continue
        if _DATE_LINE.match(line):
            h.date = line.strip()
            prev = next((p for p in reversed(lines[:idx]) if p.strip()), "")
            if h.from_ is None and _NAME_LINE.match(prev) and not _FIELD.match(prev):
                h.from_ = prev.strip()
            current = None
            continue
        if current in ("to", "cc", "bcc") and line.strip():
            values[current].append(line.strip())
    if h.from_ is None and values.get("from"):
        h.from_ = " ".join(values["from"])
    h.to = " ".join(values.get("to", []))
    h.cc = " ".join(values.get("cc", []))
    if h.date is None:
        dates = values.get("sent") or values.get("date")
        h.date = dates[0] if dates else None
    return h


def split_chain(text: str) -> list[Segment]:
    """Newest-first segments of a body that quotes or forwards earlier messages."""
    lines = text.split("\n")
    blocks: list[tuple[int, int]] = []  # (start, subject line)
    floor = 0
    for s, line in enumerate(lines):
        if len(blocks) >= MAX_SEGMENTS - 1:
            break
        if not _SUBJECT.match(line):
            continue
        start = _block_start(lines, s, floor)
        if start is None:
            continue
        blocks.append((start, s))
        floor = s + 1
    if not blocks:
        return [Segment(body=text)]
    segments = [Segment(body="\n".join(lines[: blocks[0][0]]))]
    for i, (start, s) in enumerate(blocks):
        end = blocks[i + 1][0] if i + 1 < len(blocks) else len(lines)
        headers = _parse_block(lines[start : s + 1])
        segments.append(Segment(body="\n".join(lines[s + 1 : end]), headers=headers))
    return segments


def _strip_disclaimer(lines: list[str]) -> list[str]:
    n = len(lines)
    for i, line in enumerate(lines):
        if i < n * 0.3:
            continue
        window = " ".join(lines[i : i + 4])
        if (_RULE.match(line) and _DISCLAIMER.search(window)) or (
            _DISCLAIMER.search(line) and i >= n * 0.6
        ):
            return lines[:i]
    return lines


def clean_body(text: str, *, strip_quotes: bool = True, strip_disclaimers: bool = True) -> str:
    """Normalise one body: transfer-encoding leftovers, charset artefacts ('?' for smart
    quotes), HTML entities, control characters and whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("=\n", "").replace("=20", " ").replace("=09", " ").replace("=3D", "=")
    text = html.unescape(text).replace("\xa0", " ")
    text = _CONTROL.sub("", text).replace("\t", " ")
    text = _SMART_APOS.sub("'", text)
    text = _SMART_DASH.sub(" - ", text)
    lines = [re.sub(r"^(\s*)\?\s+(?=\S)", r"\1- ", ln.rstrip()) for ln in text.split("\n")]
    if strip_quotes:
        lines = [ln for ln in lines if not ln.lstrip().startswith(">")]
    if strip_disclaimers:
        lines = _strip_disclaimer(lines)
    out: list[str] = []
    for ln in lines:
        indent = len(ln) - len(ln.lstrip(" "))
        body = re.sub(r" {2,}", " ", ln.strip())
        out.append((" " * min(indent, 4) + body) if body else "")
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")
    return cleaned.strip()


def is_garbled(text: str) -> bool:
    """Tables, encoded blobs and mostly non-text bodies are not usable as mail content."""
    visible = [c for c in text if not c.isspace()]
    if len(visible) < 20:
        return True
    letters = sum(c.isalpha() for c in visible)
    non_ascii = sum(ord(c) > 127 for c in visible)
    if letters / len(visible) < 0.6 or non_ascii / len(visible) > 0.02:
        return True
    if len(_LONG_TOKEN.findall(text)) > 2:
        return True
    lines = [ln for ln in text.split("\n") if ln.strip()]
    single = sum(len(ln.split()) == 1 for ln in lines)
    return len(lines) >= 8 and single / len(lines) > 0.6  # one word per line: broken wrapping
