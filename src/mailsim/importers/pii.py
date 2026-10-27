"""Residual-PII scanner for imported/generated material.

Flags, per file and line: any mention of Enron; known Enron people (`names.KNOWN_PEOPLE`, the
distinctive surnames alone, other surnames after a title); optional extra real names (e.g. the
registry harvested by an import run, kept outside the repository); Lotus Notes addresses and
routing fragments; phone numbers other than the fictional 555-01xx range (also glued to a
label: ``Tel.713-...``); US social security number patterns; personnel-style IDs
(``P00512345``) and mixed letter-digit deal ids (``NI7771``); postal ZIPs; street addresses
other than the fictional ``100 Commerce Street``; Enron entity acronyms (EPMI, ENA, ``TW``,
``EA``, ``TGT``, ...); named real third parties (`names.THIRD_PARTY_MAP`) and real companies,
banks, utilities, newspapers and associations (`names.REAL_ORGS_*` and any "<Name> Utilities /
Council / Inc ..." whose name is not fictional); markers of famous records
(`names.FAMOUS_MARKERS`); email addresses and web hosts outside ``.example.test``.

Digit ids: every run of 6+ digits is flagged unless it is money (``$150000``), a decimal, a
compact date (``20261014``) or one of the hashed ids the import produced (``allowed``; a
scenario pack lists them in ``pack.json`` as ``hashed_ids``). This rule is independent of the
scrubber's own digit patterns, so an id the scrubber missed is caught here.

``.eml`` files are parsed: headers, decoded text parts and the text of every attachment (PDF
strings incl. the Info dictionary and XMP metadata, every XML part of XLSX / DOCX packages,
text) are scanned, so neither transfer encodings nor attachments hide anything. An attachment
beyond the parser's size / ratio limits or one that yields no text is itself a finding.

Real full names are matched with a token-pair set lookup (first + last, an optional middle
initial in between), so scanning against a registry of tens of thousands of names is linear in
the text size.
"""

from __future__ import annotations

import email
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from email import policy
from pathlib import Path

from mailsim.importers import names as N
from mailsim.importers.attachments import (
    MAX_ATTACHMENT,
    UnreadableAttachment,
    UnsafeAttachment,
    extract_text,
)
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
_LONE_CAP = re.compile(r"\s*-*\s*([A-Z][a-z]{2,14})[.,:]?\s*")
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
        for k in sorted(N.THIRD_PARTY_NAMES | N.THIRD_PARTY_FLAG_ONLY, key=len, reverse=True)
    )
)
_THIRD_PARTY_ANYCASE = re.compile(r"\b(?:" + "|".join(N.SYSTEM_NAMES_ANYCASE) + r")", re.IGNORECASE)
_FAMOUS = re.compile("|".join(N.FAMOUS_MARKERS), re.IGNORECASE)
_STRICT_ACRONYM = re.compile(N.STRICT_ACRONYM)
# first names that are also allowed benchmark words: a person unless the benchmark follows
# the former head office's area code followed by digits (any grouping, even an unfinished one)
# a real first name kept next to a pseudonymized surname ("Bill Stellan"), or one in capitals
_REAL_FIRSTS = frozenset((set(N.COMMON_FIRST) | set(N.AMBIGUOUS_FIRST)) - set(N.FAKE_FIRST)
                         - N.PERSONA_FIRST - set(N.PUBLIC_ORGS))  # fmt: skip
_PSEUDO_SURNAMES = frozenset({*N.FAKE_LAST, *N.FAKE_FIRST})
_FIRST_PAIR = re.compile(r"(?=\b([A-Z][a-z]+) ([A-Z][a-z]+)\b)")
_CAPS_FIRST = frozenset(n.upper() for n in set(N.COMMON_FIRST) - set(N.AMBIGUOUS_FIRST)
                        - set(N.FAKE_FIRST) - N.PERSONA_FIRST if len(n) >= 3)  # fmt: skip
_HQ_AREA = re.compile(r"(?<![\d$.,])\(?713\)?[ .\-/]{0,2}\d{3}\b")
_BENCHMARK_NAME = re.compile(
    r"\bHenry\b(?! Hub)|\bBrent\b(?! (?:crude|oil|blend|futures|prices?|swaps?|index|spread))"
)
_REAL_ORG = re.compile(
    "|".join([rf"(?i:(?<![\w&]){p}(?![\w&]))" for p in N.REAL_ORGS_ANYCASE]
             + [rf"(?<![\w&]){re.escape(p)}(?![\w&])" for p in N.REAL_ORGS_CASED])
)  # fmt: skip
# "<Name> Utilities", "The <Name> Californian", "<Name> Inc.": an organisation whose name is not
# one of the fictional ones. Corporate tails need a fictional / public name to pass; group-like
# tails ("Credit Group") only fail with a proper noun that is not fictional ("Michigan Group").
_CORPORATE_TAIL = (
    r"Utilities|Utility|Gas Works|Council|Association|Bank|Bancorp|Land Office|Inc\b\.?|Incorporated|"
    r"Corp\b\.?|Corporation|LLC|LLP|L\.L\.C\.|LP|L\.P\.|Ltd\b\.?|Limited|Co\.|Company|"
    r"Airlines|Petroleum|Federation|Institute|Society|Foundation|Cooperative|Co-op|Coalition|"
    r"Alliance"
)
_GROUP_TAIL = r"Partners|Group|Holdings|Capital|Securities|Trust|Energy|Power|Gas|Electric"
_ORG_PHRASE = re.compile(
    rf"((?:[A-Z][\w&'.\-]*[ \t]+(?:(?:&|and|of)[ \t]+)?){{1,4}})"
    rf"(?:(?P<corp>{_CORPORATE_TAIL})|(?P<group>{_GROUP_TAIL}))(?![\w-])"
)
_FICTIONAL = frozenset(
    {*N.FICTIONAL_ORG_WORDS, *N.FAKE_ORG_NAMES, *N.FAKE_FIRST, *N.FAKE_LAST, *N.FAKE_PLACES,
     *(w.title() for d in N.FAKE_ORGS for w in d.split("-")),
     *N.PUBLIC_ORGS, *(w for v in (*N.ENTITY_MAP.values(), *N.THIRD_PARTY_MAP.values())
                       for w in v.split())}
) - frozenset(N.COMMON_WORDS)  # fmt: skip
_ARTICLES = frozenset(["The", "Our", "Your", "This", "That", "Its", "A", "An", "Each", "Every",
                       "Any", "Such", "Same", "Their", "My", "His", "Her"])  # fmt: skip
_GENERIC_HEAD = frozenset({*N.COMMON_WORDS, *_ARTICLES, "Coast", "East", "West", "North", "South",
                           "Central", "Old", "Natural", "Physical", "Financial", "Retail"})  # fmt: skip
# newspapers and magazines: "The Bakersfield Californian", "Riverton Chronicle" (a real place)
_NEWSPAPER = re.compile(
    r"(?:\b(The)[ \t]+)?((?:[A-Z][a-z]+[ \t]+){1,3})(?:Californian|Times|Journal|Chronicle|Herald|"
    r"Tribune|Gazette|Examiner|Post|Register|Bee|Star-Telegram|Sentinel|Inquirer)\b"
)
_DIGIT_RUN = re.compile(r"(?<![0-9])[0-9]{6,}(?![0-9])")
_ID_HEADERS = frozenset(
    ["message-id", "in-reply-to", "references", "content-type", "content-transfer-encoding",
     "mime-version", "content-disposition", "content-id"]
)  # fmt: skip
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


def _compact_date(run: str) -> bool:
    """YYYYMMDD, YYYYMMDDHHMM or YYYYMMDDHHMMSS with a plausible year, month and day."""
    if len(run) not in (8, 12, 14) or not run.startswith(("19", "20")):
        return False
    return 1 <= int(run[4:6]) <= 12 and 1 <= int(run[6:8]) <= 31


def digit_ids(text: str, allowed: frozenset[str] = frozenset()) -> list[str]:
    """6+ digit runs that are not money, a decimal, a compact date or an allowed hashed id."""
    out = []
    for m in _DIGIT_RUN.finditer(text):
        run, before, after = m.group(0), text[max(0, m.start() - 4) : m.start()], text[m.end() :]
        if run in allowed or _compact_date(run):
            continue
        if re.search(r"(?:\$|USD ?)$", before):
            continue  # money
        # a decimal (1234567.5) or a thousands group (1234567,890); "111111,222222" is a list
        # and "111111.222222" two ids, so neither excuses a run
        if re.match(r"\.\d{1,4}(?!\d)|,\d{3}(?!\d)", after):
            continue
        out.append(run)
    return out


def org_mentions(line: str) -> list[str]:
    """Real organisations in a line: the explicit lists plus non-fictional "<Name> <tail>"."""
    out = [m.group(0) for m in _REAL_ORG.finditer(line)]
    for m in _ORG_PHRASE.finditer(line):
        heads = [w.strip(".,'") for w in m.group(1).split()]
        heads = [w for w in heads if w and w not in ("&", "and", "of")]
        if any(w in _FICTIONAL for w in heads):
            continue
        named = [w for w in heads if w not in _ARTICLES]
        proper = [w for w in heads if w not in _GENERIC_HEAD]
        if (m.group("corp") and named) or proper:
            out.append(m.group(0).strip())
    for m in _NEWSPAPER.finditer(line):
        heads = m.group(2).split()
        if any(w in _FICTIONAL for w in heads):
            continue
        if m.group(1) or any(w in N.PLACES for w in heads):
            out.append(m.group(0).strip())
    return list(dict.fromkeys(out))


def _kind(exc: UnsafeAttachment) -> str:
    return "unreadable-attachment" if isinstance(exc, UnreadableAttachment) else "unsafe-attachment"


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

    def scan_text(self, text: str, path: str = "<text>", *, allowed: frozenset[str] = frozenset(),
                  digits: bool = True) -> list[Finding]:  # fmt: skip
        """Findings in `text`; `allowed` are hashed digit ids that may appear (`digits=False`
        skips the digit-id rule, for message ids and MIME boundaries)."""
        out: list[Finding] = []
        lines = text.splitlines()
        for no, line in enumerate(lines, start=1):
            # same whitespace normalisation as clean_body (tabs, NBSP)
            norm = line.replace("\t", " ").replace("\xa0", " ")
            out.extend(self._line(norm, path, no))
            if digits:
                out.extend(Finding(path, no, "digit-id", run) for run in digit_ids(norm, allowed))
        for no in range(1, len(lines)):  # a name or a firm broken over two lines
            tail, head = lines[no - 1].split(), lines[no].split()
            if tail and head:
                pair = f"{tail[-1]} {head[0]}"
                out.extend(
                    f for f in self._line(pair, path, no) if f.kind in ("real-name", "real-surname")
                )
                joined = f"{lines[no - 1].strip()} {lines[no].strip()}"
                out.extend(Finding(path, no, "real-org", name) for name in org_mentions(joined)
                           if name not in lines[no - 1] and name not in lines[no])  # fmt: skip
                wrapped = f"{' '.join(tail[-3:])} {' '.join(head[:3])}"  # a number split by a wrap
                split = len(" ".join(tail[-3:]))
                for m in PHONE_RUN.finditer(wrapped):
                    decimals = re.findall(r"\d\.(\d+)", m.group(0))  # "6.14 245600.0": numbers
                    if (is_phone(m.group(0)) and m.start() < split < m.end()
                            and len(re.sub(r"\D", "", m.group(0))) >= 10
                            and all(len(d) in (3, 4) for d in decimals)):  # fmt: skip
                        out.append(Finding(path, no, "phone", m.group(0)))
                for kind, rx in (("landmark", _LANDMARK), ("famous-record", _FAMOUS)):
                    out.extend(
                        Finding(path, no, kind, m.group(0))
                        for m in rx.finditer(joined)
                        if m.group(0) not in lines[no - 1] and m.group(0) not in lines[no]
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
        for m in _HQ_AREA.finditer(line):
            if not any(f.kind == "phone" and m.group(0) in f.match for f in out):
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
        for m in _BENCHMARK_NAME.finditer(line):
            hit("real-first-name", m.group(0))
        for m in _FIRST_PAIR.finditer(line):
            if m.group(1) in _REAL_FIRSTS and m.group(2) in _PSEUDO_SURNAMES:
                hit("real-first-name", f"{m.group(1)} {m.group(2)}")
        for tok in re.findall(r"\b[A-Z]{3,}\b", line):
            if tok in _CAPS_FIRST:
                hit("real-first-name", tok)
        for m in _STRICT_ACRONYM.finditer(line):
            hit("enron-acronym", m.group(0))
        for name in org_mentions(line):
            hit("real-org", name)
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
        for tok in re.findall(r"\b[A-Z]{3,4}\b|\b[A-Z]{1,2}&[A-Z]\b", line):
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

    def scan_message(self, raw: bytes, path: str = "<message>", *,
                     allowed: frozenset[str] = frozenset()) -> list[Finding]:  # fmt: skip
        """Headers, decoded text parts and attachment text of one RFC 5322 message."""
        msg = email.message_from_bytes(raw, policy=policy.default)
        out: list[Finding] = []
        for part in msg.walk():
            for k, v in part.items():
                ids = k.lower() in _ID_HEADERS
                out.extend(self.scan_text(f"{k}: {v}", f"{path}#headers", allowed=allowed,
                                          digits=not ids))  # fmt: skip
        for no, part in enumerate(msg.walk()):
            if part.is_multipart():
                continue
            data = part.get_payload(decode=True)
            payload = data if isinstance(data, bytes) else b""
            name = part.get_filename() or ""
            ctype = part.get_content_type()
            label = f"{path}#{name or ctype}" if name else f"{path}#part{no}"
            if name:
                out.extend(self.scan_text(name, f"{label}:filename", allowed=allowed))
            if ctype.startswith("text/") and not name:
                text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            else:
                try:
                    text = extract_text(payload, name, ctype)
                except UnsafeAttachment as exc:
                    out.append(Finding(label, 0, _kind(exc), str(exc)))
                    continue
                if payload.strip() and not text.strip():
                    out.append(Finding(label, 0, "unreadable-attachment", name or ctype))
            out.extend(self.scan_text(text, label, allowed=allowed))
        return out

    @staticmethod
    def _manifest(f: Path, cache: dict[Path, frozenset[str]]) -> frozenset[str]:
        """Hashed ids listed by the nearest enclosing scenario pack (``pack.json``)."""
        for d in f.parents:
            if d in cache:
                return cache[d]
            pack = d / "pack.json"
            if pack.is_file():
                try:
                    ids = json.loads(pack.read_text(encoding="utf-8")).get("hashed_ids", [])
                except ValueError:
                    ids = []
                cache[d] = frozenset(str(i) for i in ids)
                return cache[d]
        return frozenset()

    def scan_paths(self, paths: Iterable[Path]) -> list[Finding]:
        out: list[Finding] = []
        cache: dict[Path, frozenset[str]] = {}
        for root in paths:
            files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
            for f in files:
                if f.stat().st_size > MAX_ATTACHMENT:
                    out.append(
                        Finding(str(f), 0, "unsafe-attachment", "file larger than the limit")
                    )
                    continue
                data = f.read_bytes()
                allowed = self._manifest(f.resolve(), cache)
                if f.suffix.lower() == ".eml":
                    out.extend(self.scan_message(data, str(f), allowed=allowed))
                    continue
                try:
                    text = extract_text(data, f.name)
                except UnsafeAttachment as exc:
                    out.append(Finding(str(f), 0, _kind(exc), str(exc)))
                    continue
                # the digit-id rule is for mail and attachments (configuration files hold
                # durations and counters)
                out.extend(self.scan_text(text, str(f), allowed=allowed, digits=False))
        return out
