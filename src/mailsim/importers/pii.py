"""Residual-PII scanner for imported/generated material.

Flags, per file and line: any mention of Enron; known Enron people (`names.KNOWN_PEOPLE`, the
distinctive surnames alone, other surnames after a title); optional extra real names (e.g. the
registry harvested by an import run, kept outside the repository); Lotus Notes addresses and
routing fragments; phone numbers other than the fictional 555-01xx range; US social security
number patterns; personnel-style IDs (``P00512345``); the former head-office ZIP; Enron entity
acronyms (EPMI, ENA, ...); email addresses and web hosts outside ``.example.test``.

Real full names are matched with a token-pair set lookup (first + last, an optional middle
initial in between), so scanning against a registry of tens of thousands of names is linear in
the text size.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mailsim.importers import names as N
from mailsim.importers.pseudonymize import (
    ALNUM_ID,
    IPV4,
    PHONE_RUN,
    PHONE_SHORT,
    SSN,
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
        for no, line in enumerate(text.splitlines(), start=1):
            # same whitespace normalisation as clean_body (tabs, NBSP)
            out.extend(self._line(line.replace("\t", " ").replace("\xa0", " "), path, no))
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
        for m in _SURNAME_AFTER_TITLE.finditer(line):
            if m.group(1).lower() in self.titled:
                hit("real-surname", m.group(0))
        for m in _LOTUS.finditer(line):
            hit("lotus-notes", m.group(0))
        for m in PHONE_RUN.finditer(line):
            if is_phone(m.group(0)):
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

    def scan_paths(self, paths: Iterable[Path]) -> list[Finding]:
        out: list[Finding] = []
        for root in paths:
            files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
            for f in files:
                text = f.read_bytes().decode("utf-8", "replace")
                out.extend(self.scan_text(text, str(f)))
        return out
