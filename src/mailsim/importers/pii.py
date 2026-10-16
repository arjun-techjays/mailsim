"""Residual-PII scanner for imported/generated material.

Flags, per file and line: any mention of Enron; known Enron people (`names.KNOWN_PEOPLE`, the
distinctive surnames alone, other surnames after a title); optional extra real names (e.g. the
registry harvested by an import run, kept outside the repository); Lotus Notes addresses and
routing fragments; phone numbers other than the fictional 555-01xx range; US social security
number patterns; email addresses and web hosts outside ``.example.test``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mailsim.importers import names as N
from mailsim.importers.pseudonymize import IPV4, PHONE, SSN

_ENRON = re.compile(r"enron", re.IGNORECASE)
_LOTUS = re.compile(
    r"[A-Za-z]/(?:HOU|ECT|NA|Corp|ENRON|Enron|EES|EU|LON)\b|@(?:ECT|ENRON|Enron|EES|enronXgate)\b"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+'\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")
_HOST = re.compile(
    r"\b(?:https?://)?((?:[a-z0-9\-]+\.)+(?:com|net|org|edu|gov|mil|co\.uk|uk|ca|de|fr|biz|"
    r"info|us|io|jp|au|in))\b",
    re.IGNORECASE,
)
_FAKE_PHONE = re.compile(r"555-01\d\d|x55\d\d|555-555-01\d\d")
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
        full = {n.lower() for n in N.KNOWN_PEOPLE}
        full.update(n.lower() for n in extra_names if len(n.split()) >= 2)
        self.full_names = full
        last_counts: dict[str, int] = {}
        for name in full:
            last = name.split()[-1]
            last_counts[last] = last_counts.get(last, 0) + 1
        self.surnames = {s.lower() for s in N.DISTINCTIVE_SURNAMES}
        known_last = {n.split()[-1] for n in (x.lower() for x in N.KNOWN_PEOPLE)}
        self.titled = known_last
        self._full_rx = self._compile(sorted(full))

    @staticmethod
    def _compile(names: list[str]) -> re.Pattern[str] | None:
        if not names:
            return None  # pragma: no cover - KNOWN_PEOPLE is never empty
        alts = "|".join(re.escape(n).replace(r"\ ", r"\s+(?:[A-Z]\.?\s+)?") for n in names)
        return re.compile(rf"\b(?:{alts})\b", re.IGNORECASE)

    def scan_text(self, text: str, path: str = "<text>") -> list[Finding]:
        out: list[Finding] = []
        for no, line in enumerate(text.splitlines(), start=1):
            out.extend(self._line(line, path, no))
        return out

    def _line(self, line: str, path: str, no: int) -> list[Finding]:
        out: list[Finding] = []

        def hit(kind: str, match: str) -> None:
            out.append(Finding(path, no, kind, match))

        for m in _ENRON.finditer(line):
            hit("enron", line[max(0, m.start() - 10) : m.end() + 10])
        if self._full_rx is not None:
            for m in self._full_rx.finditer(line):
                hit("real-name", m.group(0))
        for tok in re.findall(r"\b[A-Za-z][a-z]+\b", line):
            if tok.lower() in self.surnames:
                hit("real-surname", tok)
        for m in _SURNAME_AFTER_TITLE.finditer(line):
            if m.group(1).lower() in self.titled:
                hit("real-surname", m.group(0))
        for m in _LOTUS.finditer(line):
            hit("lotus-notes", m.group(0))
        for m in PHONE.finditer(line):
            if not _FAKE_PHONE.search(m.group(0)):
                hit("phone", m.group(0))
        for m in SSN.finditer(line):
            if m.group(0) != "000-00-0000":
                hit("ssn", m.group(0))
        for m in _EMAIL.finditer(line):
            if not _allowed_domain(m.group(1)):
                hit("email-domain", m.group(0))
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
