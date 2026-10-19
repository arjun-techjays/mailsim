"""Residual-PII scanner for imported/generated material.

Flags, per file and line: any mention of Enron; known Enron people (`names.KNOWN_PEOPLE`, the
distinctive surnames alone, other surnames after a title); optional extra real names (e.g. the
registry harvested by an import run, kept outside the repository); Lotus Notes addresses and
routing fragments; phone numbers other than the fictional 555-01xx range (also glued to a
label: ``Tel.713-...``); US social security number patterns; personnel-style IDs
(``P00512345``) and mixed letter-digit deal ids (``NI7771``); postal ZIPs; street addresses
other than the fictional ``100 Commerce Street``; Enron entity acronyms (EPMI, ENA, ``TW``,
...); named real third parties (`names.THIRD_PARTY_MAP`); markers of famous records
(`names.FAMOUS_MARKERS`); email addresses and web hosts outside ``.example.test``.

``.eml`` files are parsed: headers, decoded text parts and the text of every attachment (PDF,
XLSX, text) are scanned, so neither transfer encodings nor attachments hide anything.

Real full names are matched with a token-pair set lookup (first + last, an optional middle
initial in between), so scanning against a registry of tens of thousands of names is linear in
the text size.
"""

from __future__ import annotations

import email
import re
from collections.abc import Iterable
from dataclasses import dataclass
from email import policy
from pathlib import Path

from mailsim.importers import names as N
from mailsim.importers.attachments import extract_text
from mailsim.importers.pseudonymize import (
    ALNUM_ID,
    DEAL_ALNUM,
    GLUED_PHONE,
    INTL_PHONE,
    IPV4,
    PHONE_RUN,
    PHONE_SHORT,
    PO_BOX,
    SSN,
    STREET,
    TW,
    UK_POSTCODE,
    ZIP_HQ,
    denylisted,
    is_phone,
)

_ENRON = re.compile(r"enron", re.IGNORECASE)
_LOTUS = re.compile(
    r"[A-Za-z]/(?:HOU|ECT|NA|Corp|ENRON|Enron|EES|EU|LON)\b|@(?:ECT|ENRON|Enron|EES|enronXgate)\b"
)
# also address fragments ("jdoe@corp-" broken over a line): any word@word outside example.test
_EMAIL = re.compile(r"[A-Za-z0-9._%+'\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*)")
_HOST = re.compile(
    r"\b(?:https?://)?((?:[a-z0-9\-]+\.)+(?:com|net|org|edu|gov|mil|co\.uk|uk|ca|de|fr|biz|"
    r"info|us|io|jp|au|in))\b",
    re.IGNORECASE,
)
_FAKE_PHONE = re.compile(r"555-01\d\d|x55\d\d|555-555-01\d\d")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*\.?")
_SURNAME_AFTER_TITLE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.? ([A-Z][a-z]+)")
_LONE_LOWER = re.compile(r"\s*-*\s*([a-z]{3,15})\.?\s*|.*\s-([a-z]{3,15})\s*")
_LOWER_POSSESSIVE = re.compile(r"(?<![\w@.'-])([a-z]{3,15})'s\b")
_COMMON_WORDS_LOWER = frozenset(
    [
        "today",
        "tomorrow",
        "yesterday",
        "everyone",
        "everybody",
        "nobody",
        "somebody",
        "someone",
        "anyone",
        "company",
        "year",
        "month",
        "week",
        "day",
        "world",
        "market",
        "industry",
        "team",
        "group",
        "client",
        "customer",
        "client's",
    ]
)
_LONE_CAP = re.compile(r"\s*-*\s*([A-Z][a-z]{2,14})\.?\s*")
_LOWER_GREETING = re.compile(r"\s*(?i:hi|hey|hello|dear|morning|thanks)[ ,]+([a-z]{3,15})\b")
_LONE_WORDS = frozenset(
    [
        "thanks",
        "regards",
        "cheers",
        "yes",
        "yeah",
        "sure",
        "fine",
        "done",
        "later",
        "best",
        "fyi",
        "okay",
        "thx",
        "tia",
        "great",
        "agreed",
        "noted",
        "perfect",
        "correct",
        "right",
        "wrong",
        "maybe",
        "will",
        "can",
        "may",
        "june",
        "april",
        "august",
        "bill",
        "mark",
    ]
)
_PAIR = re.compile(r"(?=\b([A-Z][a-z]+) ([A-Z][a-z]+)\b)")
_FAKE_FIRST = frozenset(N.FAKE_FIRST)
_THIRD_PARTY = re.compile(
    "|".join(
        rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])"
        if k.islower() or k.isupper() and len(k) > 3
        else rf"(?<![A-Za-z]){re.escape(k)}(?![a-z])"
        for k in sorted(N.THIRD_PARTY_NAMES, key=len, reverse=True)
    )
)
_THIRD_PARTY_ANYCASE = re.compile(r"enerfax|napster|nisource|prebon", re.IGNORECASE)
_FAMOUS = re.compile("|".join(N.FAMOUS_MARKERS), re.IGNORECASE)
_LANDMARK = re.compile("|".join(N.LANDMARKS), re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    match: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.match!r}"


def _allowed_domain(domain: str) -> bool:
    d = domain.lower().rstrip(".")
    return d == "example.test" or d.endswith(".example.test")


class Scanner:
    def __init__(self, extra_names: Iterable[str] = ()) -> None:
        pairs = set()
        for name in [*N.KNOWN_PEOPLE, *extra_names]:
            parts = name.lower().split()
            if len(parts) >= 2:
                pairs.add((parts[0], parts[-1]))
        self.full_names = pairs
        # real first names written in lower case where a signature goes ("thanks\nbrant")
        # known real people's first names written alone on a sign-off line ("Dutch")
        self.known_first = (
            {n.split()[0] for n in N.KNOWN_PEOPLE} - set(N.FAKE_FIRST) - N.PERSONA_FIRST
        )
        self.first_lower = (
            {n.lower().split()[0] for n in [*N.KNOWN_PEOPLE, *extra_names] if len(n.split()) >= 2}
            - {f.lower() for f in N.FAKE_FIRST}
            - _LONE_WORDS
        )
        self.surnames = {s.lower() for s in N.DISTINCTIVE_SURNAMES}
        self.titled = {n.split()[-1].lower() for n in N.KNOWN_PEOPLE}

    def _names(self, line: str) -> list[str]:
        toks = [(m.group(0), m.group(0).lower().rstrip(".")) for m in _WORD.finditer(line)]
        out = []
        for i, (raw, low) in enumerate(toks):
            for j in (i + 1, i + 2):
                if j >= len(toks):
                    break
                if j == i + 2 and len(toks[i + 1][1]) != 1:
                    break  # only a single middle initial may sit in between
                if (low, toks[j][1]) in self.full_names:
                    out.append(f"{raw} {toks[j][0]}")
        return out

    def scan_text(self, text: str, path: str = "<text>") -> list[Finding]:
        out: list[Finding] = []
        lines = text.splitlines()
        for no, line in enumerate(lines, start=1):
            # same whitespace normalisation as clean_body (tabs, NBSP)
            out.extend(self._line(line.replace("\t", " ").replace("\xa0", " "), path, no))
        for no in range(1, len(lines)):  # a name pair broken over two lines
            tail, head = lines[no - 1].split(), lines[no].split()
            if tail and head:
                pair = f"{tail[-1]} {head[0]}"
                out.extend(
                    f for f in self._line(pair, path, no) if f.kind in ("real-name", "real-surname")
                )
        return out

    def _line(self, line: str, path: str, no: int) -> list[Finding]:
        out: list[Finding] = []

        def hit(kind: str, match: str) -> None:
            out.append(Finding(path, no, kind, match))

        for m in _ENRON.finditer(line):
            hit("enron", line[max(0, m.start() - 10) : m.end() + 10])
        for name in self._names(line):
            hit("real-name", name)
        for tok in re.findall(r"\b[A-Z][a-zA-Z]+\b", line):
            if denylisted(tok):
                hit("denylisted-name", tok)
        for tok in re.findall(r"\b[A-Za-z][a-z]+\b", line):
            if tok.lower() in self.surnames:
                hit("real-surname", tok)
        lone = _LONE_LOWER.fullmatch(line)
        word = (lone.group(1) or lone.group(2)) if lone else None
        cap = _LONE_CAP.fullmatch(line)
        if cap and cap.group(1) in self.known_first:
            hit("real-name", cap.group(1))
        for m in _LOWER_POSSESSIVE.finditer(line):  # "nicole's address"
            if m.group(1) in self.first_lower and m.group(1) not in _COMMON_WORDS_LOWER:
                hit("real-name", m.group(0))
        greet = _LOWER_GREETING.match(line)
        for w in (word, greet.group(1) if greet else None):
            if w and w in self.first_lower:
                hit("real-name", w)
        for m in _SURNAME_AFTER_TITLE.finditer(line):
            if m.group(1).lower() in self.titled:
                hit("real-surname", m.group(0))
        for m in _PAIR.finditer(line):  # a pseudonymized first name next to a real surname
            if m.group(1) in _FAKE_FIRST and m.group(2).lower() in self.titled:
                hit("real-surname", f"{m.group(1)} {m.group(2)}")
        for m in _LOTUS.finditer(line):
            hit("lotus-notes", m.group(0))
        line = GLUED_PHONE.sub(lambda m: m.group(0) + " ", line)
        for m in PHONE_RUN.finditer(line):
            if is_phone(m.group(0)):
                hit("phone", m.group(0))
        for m in INTL_PHONE.finditer(line):
            hit("phone", m.group(0))
        for m in PHONE_SHORT.finditer(line):
            if not _FAKE_PHONE.search(m.group(0)):
                hit("phone", m.group(0))
        for m in SSN.finditer(line):
            if m.group(0) != "000-00-0000":
                hit("ssn", m.group(0))
        for m in _EMAIL.finditer(line):
            if not _allowed_domain(m.group(1)):
                hit("email-domain", m.group(0))
        for m in ALNUM_ID.finditer(line):
            hit("personnel-id", m.group(0))
        for m in DEAL_ALNUM.finditer(line):
            hit("deal-id", m.group(0))
        for m in STREET.finditer(line):
            hit("street", m.group(0))
        for m in PO_BOX.finditer(line):
            if m.group(0) != "P.O. Box 100":
                hit("street", m.group(0))
        for m in UK_POSTCODE.finditer(line):
            hit("zip", m.group(0))
        for m in TW.finditer(line):
            hit("enron-acronym", m.group(0))
        third = {(m.start(), m.group(0)) for m in _THIRD_PARTY.finditer(line)}
        third |= {(m.start(), m.group(0)) for m in _THIRD_PARTY_ANYCASE.finditer(line)
                  if not any(s <= m.start() < s + len(t) for s, t in third)}  # fmt: skip
        for _, match in sorted(third):
            hit("third-party", match)
        for m in _FAMOUS.finditer(line):
            hit("famous-record", m.group(0))
        for m in _LANDMARK.finditer(line):
            hit("landmark", m.group(0))
        for m in ZIP_HQ.finditer(line):
            hit("zip", m.group(0))
        for name in N.ENTITY_NAMES:
            if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", line, re.IGNORECASE):
                hit("enron-entity", name)
        for tok in re.findall(r"\b[A-Z]{3,4}\b", line):
            if tok in N.ENRON_ACRONYMS:
                hit("enron-acronym", tok)
        for m in IPV4.finditer(line):
            if not m.group(0).startswith("192.0.2."):
                hit("ip-address", m.group(0))
        for m in _HOST.finditer(line):
            before = line[max(0, m.start() - 1) : m.start()]
            if before == "@" and _EMAIL.search(line[max(0, m.start() - 64) : m.end()]):
                continue  # the domain of an address: reported above
            if not _allowed_domain(m.group(1)):
                hit("domain", m.group(1))
        return out

    def scan_message(self, raw: bytes, path: str = "<message>") -> list[Finding]:
        """Headers, decoded text parts and attachment text of one RFC 5322 message."""
        msg = email.message_from_bytes(raw, policy=policy.default)
        out: list[Finding] = []
        headers = "\n".join(f"{k}: {v}" for k, v in msg.items())
        out.extend(self.scan_text(headers, f"{path}#headers"))
        for no, part in enumerate(msg.walk()):
            if part.is_multipart():
                continue
            data = part.get_payload(decode=True)
            payload = data if isinstance(data, bytes) else b""
            name = part.get_filename() or ""
            ctype = part.get_content_type()
            label = f"{path}#{name or ctype}" if name else f"{path}#part{no}"
            if name:
                out.extend(self.scan_text(name, f"{label}:filename"))
            if ctype.startswith("text/") and not name:
                text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            else:
                text = extract_text(payload, name, ctype)
            out.extend(self.scan_text(text, label))
        return out

    def scan_paths(self, paths: Iterable[Path]) -> list[Finding]:
        out: list[Finding] = []
        for root in paths:
            files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
            for f in files:
                data = f.read_bytes()
                if f.suffix.lower() == ".eml":
                    out.extend(self.scan_message(data, str(f)))
                else:
                    out.extend(self.scan_text(extract_text(data, f.name), str(f)))
        return out
