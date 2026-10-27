"""Pseudonymization: real identifiers -> stable placeholders -> fictional identities.

Two stages, so a generator can bind the people of a message to the personas of a scenario:

1. `Pseudonymizer.apply` rewrites text into a *template*: people become placeholders
   ``{{p:12:n}}`` (registered person 12, full name; ``f`` first, ``l`` last, ``e`` email),
   first-name-only mentions ``{{q:3:f}}``, surname-only mentions ``{{s:4:l}}``, other email
   addresses ``{{x:5:e}}`` and the distinctive part of third-party firm names ``{{o:6:n}}``
   ("Blue Flame Propane Inc." -> ``{{o:6:n}} Propane Inc.``) and place / asset names that read
   like people (plant sites, hubs, venues: "at the X", "X Power Pool") ``{{l:7:n}}``. Enron and
   its entities become the
   fictional company (`names.ENTITY_MAP`), named third parties become fictional ones
   (`names.THIRD_PARTY_MAP`); URLs, phone numbers, street addresses and mixed letter-digit deal
   ids are replaced outright, and every identifier-like digit run (deal, notice and contract
   numbers, any 6+ digit run that is not money, a decimal or a date) becomes a keyed hash
   (HMAC-SHA256 with the committed id key, which keeps the hashes stable across imports).
   The hashed outputs of a record are listed in the record (``ids``) so the scanner can tell
   them from a digit run the scrubber missed. Templates contain no real names that the
   detectors recognised.
2. `Renderer.render` turns a template into text, using an explicit binding (placeholder ->
   identity, e.g. a scenario persona) and otherwise a deterministic pseudonym derived from
   the seed (same seed and id -> same fictional name, everywhere).

Detection is rule-based (no external services): a `Registry` harvested from the corpus itself
(Lotus Notes addresses, first.last@ addresses, quoted header fields, display names, the
`names.KNOWN_PEOPLE` seed list) plus a lower-case vocabulary of the corpus that tells ordinary
words ("mark", "grant") from names. Limits are documented in docs/leadership-inboxes.md.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from mailsim.importers import names as N

PLACEHOLDER = re.compile(r"\{\{([pqsxol]):(\d+):([nfle])\}\}")
_NAME_RX = r"[A-Z][a-zA-Z'\-]+"
_PERSON = rf"{_NAME_RX}(?:\s[A-Z]\.?)?(?:\s{_NAME_RX}){{1,2}}"
_UNIT = r"[A-Za-z_]{2,20}(?: (?:Communications|COMMUNICATIONS|Development|DEVELOPMENT))?"
LOTUS = re.compile(
    rf"(?<![\w/])({_PERSON})\s?/\s?({_UNIT})((?:/{_UNIT}){{0,3}})(\s?@\s?{_UNIT})?(?![\w.])"
)
LOTUS_AT = re.compile(
    rf"(?<![\w/])({_PERSON})\s?@\s?(?:ENRON|Enron|ECT|EES|enronXgate|ENRON_DEVELOPMENT)"
    r"(?: COMMUNICATIONS| Communications)?(?![\w.@])"
)
EMAIL = re.compile(
    r"(?<![\w.%+'-])[A-Za-z0-9][A-Za-z0-9._%+'\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*"
    r"\.[A-Za-z]{2,}\b"
)
HOST = re.compile(
    r"(?<![\w@.-])((?:[a-z0-9\-]+\.)+(?:com|net|org|edu|gov|mil|co\.uk|uk|ca|de|fr|biz|info|us|io|jp"
    r"|au|in))\b(?!\.\w)",
    re.IGNORECASE,
)
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_DISPLAY = re.compile(r"\"?([A-Za-z][A-Za-z .,'\-]{1,60}?)\"?\s*<([^<>\s]+@[^<>\s]+)>")
_LAST_FIRST = re.compile(rf"\b({_NAME_RX}), ({_NAME_RX})(?: [A-Z]\.?)?(?![\w'])")
_TITLED = re.compile(rf"\b(Mr|Mrs|Ms|Dr|Miss|Messrs)\.? ({_NAME_RX})(?: ({_NAME_RX}))?")
_FULL = re.compile(rf"\b({_NAME_RX})(?: ([A-Z])\.?)? ({_NAME_RX})\b")
_SINGLE = re.compile(rf"\b{_NAME_RX}\b")
_GREETING_WORDS = r"(?:Hi|Hello|Dear|Hey|Morning|Good morning|Good afternoon|Thanks|Thank you)"
_SIGNOFF_WORDS = re.compile(
    r"(?:Thanks|Thank you|Regards|Best|Cheers|Sincerely|Best regards|Kind regards|Rgds)"
    r"[,!.]?\s*-*\s*$",
    re.IGNORECASE,
)
# Phone numbers: one generic rule (any run of digit groups separated only by space . - / or
# parentheses, optional +/00/011 prefix, 7+ digits in total, see `is_phone`) plus short
# extensions (x1234, ext. 3-1234, 87-7454).
PHONE_RUN = re.compile(
    r"(?<![\w.,$/\-\u2013\u2212])(?<!\d[:#])(?:\+\s?)?\(?\d{1,7}\)?"
    r"(?:(?:[ .\-/\u2013\u2212]|\)\s?|\s?\(|\s-\s?|-\s)\(?\d{1,7}\)?){1,6}"
    r"(?![\w:]|[.,]\d)"
)
PHONE_SHORT = re.compile(
    r"\b(?:ext\.?|x|extension)[\s-]?\d{4,5}\b|\b(?:ext\.?|x)\s?\d[-.]\d{4}\b"
    r"|(?<![\w/.$,-])\d{1,2}-\d{4}\b(?![\w/-])",
    re.IGNORECASE,
)
# "+44 1105 695188", "+1 713 555 0100": an international prefix makes a phone of any grouping
INTL_PHONE = re.compile(r"(?<![\w+])\+\s?\d{1,3}(?:[\s.\-]?\(?\d{1,4}\)?){1,4}[\s.\-]?\d{3,10}\b")
_DATE_RUN = re.compile(
    r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(?:\s[-+]\d{4})?(?:\s?-\s?\d{1,2}/\d{1,2}/\d{2,4})?"
    r"|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"
    r"|(?:1[89]|20)\d\d(?:\s?[-/]\s?(?:1[89]|20)\d\d)+|(?:(?:19|20)\d\d\s)+(?:19|20)\d\d"
)
_FAKE_PHONE_RUN = re.compile(r"(?:\(555\) |555[-/ ])?555-01\d\d")


def is_phone(run: str) -> bool:
    """A digit-group run (from PHONE_RUN) that is a phone number: 7+ digits, not a date,
    year range, IP address, decimal number or the fictional 555-01xx form. A run separated by
    single spaces only needs 3+ groups (so "2025 150" in a table is not a phone)."""
    text = run.strip()
    if len(re.sub(r"\D", "", text)) < 7:
        return False
    if _DATE_RUN.fullmatch(text.strip("()")) or _FAKE_PHONE_RUN.fullmatch(text):
        return False
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}|\d{5}-\d{4}", text):
        return False  # SSN and ZIP+4 shapes: scrubbed and scanned by their own rules
    if re.fullmatch(r"\d{6}(?:\s?[,/]\s?\d{6})+", text):
        return False  # deal-number lists (481620/481621): hashed by ANY_DIGITS
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", text) or re.fullmatch(r"\d+\.\d{1,2}", text):
        return False
    if text.startswith("+"):
        return True  # "+44 1105 695188": an international number
    groups = re.findall(r"\d+", text)
    seps = re.findall(r"[^\d]+", text.lstrip("+("))
    if all(sep == " " for sep in seps) and len(groups) < 3:
        # "713 8531234" / "0207 7834040": a 7-digit subscriber number, 10-11 digits in total
        return len(groups) == 2 and len(groups[1]) == 7 and 10 <= len("".join(groups)) <= 11
    return True


# labelled ids: "deal 481620", "Notice 2001043", "Gridbook#'s 464394", "PO no. 12345"
DEAL_NO = re.compile(
    r"(?i)\b((?:deals?|contracts?|confirm(?:ation)?s?|tickets?|K|notices?|postings?|orders?|po|"
    r"invoices?|ref(?:erence)?|nom(?:ination)?s?|id|gridbook|tidewater|trax|sitara|tagg|enpower|"
    r"ctr|account|acct)\s*(?:#'?s?|nos?\.?|numbers?)?\s*:?\s*)(\d{5,9})\b"
)
HASH_ID = re.compile(r"(?<![\w&])(#\s?)(\d{5,9})\b")  # "#549048", "# 706294"
# any other run of 6+ digits that is not money ($150000), a decimal (0.1234567, 1234567.5) or a
# thousands group (1,234567 is not one): an identifier, hashed. Date-like runs (20011014) stay.
ANY_DIGITS = re.compile(r"(?<![\d$])(?<!\d\.)\d{6,}(?!\d|\.\d|,\d{3}(?!\d))")
PASSCODE = re.compile(
    r"(?i)\b((?:pass\s?code|access\s?code|code|pin|password|conf(?:erence)?\s?id)\s*[:#]?\s*)"
    r"(\d{3,})"
)
# "This is Li Sun", "my name is ...": the words after an introduction are a name
_INTRO = re.compile(
    rf"(?i:\b(?:this is|my name is|i am|i'm|name:)\s+)({_NAME_RX}(?:\s+{_NAME_RX})?)\b"
)
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
ALNUM_ID = re.compile(r"\b[A-Z]{1,3}\d{6,}\b")  # personnel / member numbers (P00512345)
_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
    "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
)
_STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|"
    "Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|"
    "Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|Ohio|Oklahoma|Oregon|"
    "Pennsylvania|Tennessee|Texas|Utah|Vermont|Virginia|Washington|Wisconsin|Wyoming|"
    "New York|New Jersey|New Mexico|New Hampshire|North Carolina|North Dakota|South Carolina|"
    "South Dakota|Rhode Island|West Virginia"
)
FAKE_ZIP = N.FAKE_ZIP
# ZIP codes in postal addresses (state + ZIP, any ZIP+4) and the former head-office ZIP anywhere
ZIP_HQ = re.compile(
    rf"\b77002(?:-\d{{4}})?\b|\b(?:{_STATE_CODES})[.?]?,?\s+(?!{N.FAKE_ZIP}\b)\d{{5}}(?:-\d{{4}})?\b"
    rf"|\b(?:{_STATE_NAMES}),?\s+(?!{N.FAKE_ZIP}\b)\d{{5}}(?:-\d{{4}})?\b"
    rf"|\b(?!{N.FAKE_ZIP}-0000)\d{{5}}-\d{{4}}\b"
)


def _fake_zip(m: re.Match[str]) -> str:
    text = m.group(0)
    head = re.match(r"\D*", text)
    prefix = head.group(0) if head else ""
    return prefix + (FAKE_ZIP + "-0000" if "-" in text[len(prefix) :] else FAKE_ZIP)


# street addresses: "1400 Smith Street", "55 East 52nd Street", "1221 N. Lamar Blvd."
STREET = re.compile(
    rf"\b(?!{re.escape(N.FAKE_STREET)})\d{{1,5}}(?:-\d{{1,5}})?\s+"
    r"(?:(?:[NSEW]{1,2}\.?|North|South|East|West|Northeast|Northwest|Southeast|Southwest)\s+)?"
    r"(?:\d{1,3}(?:st|nd|rd|th)|[A-Z][a-z]*)(?:\s+[A-Z][a-z]+){0,2}\s+(?:Street|St\b\.?|Avenue|"
    r"Ave\b\.?|Road|Rd\b\.?|Boulevard|Blvd\b\.?|Drive|Dr\b\.?|Lane|Ln\b\.?|Parkway|Pkwy\b\.?|"
    r"Corridor|Way|Court|Ct\b\.?|Circle|Place|Pl\b\.?|Highway|Hwy\b\.?|Freeway|Fwy\b\.?|Trail|"
    r"Terrace|Plaza|Square|Loop|Center|Centre|Tower)(?![A-Za-z])"
)
# "Tel.713-853-1234": a label glued to the number hides it from the phone rule
GLUED_PHONE = re.compile(r"(?<=[A-Za-z])[.:](?=\(?\d{3}\)?[\s.\-/]?\d{3}[\s.\-/]?\d{4}\b)")
# mixed letter-digit deal / ticket ids ("NI7771", "N17872.9"); years (FY2001, Q3...) excepted.
# Replaced by "CX" + same-length hashed digits.
DEAL_ALNUM = re.compile(
    r"\b(?!(?:FY|CY|Q[1-4]|H[12]|CX|CV)\d)[A-Z]{1,3}(\d{4,5})((?:\.[0-9A-Z]{1,2})?)\b"
)
TW = re.compile(N.TW)
STRICT_ACRONYM = re.compile(N.STRICT_ACRONYM)
# UK postcodes after a place ("London E1 7JF"); the fictional replacement is not a real district
UK_POSTCODE = re.compile(r"(?<=[A-Za-z,] )(?!ZZ1 1ZZ)[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}\b")
FAKE_POSTCODE = "ZZ1 1ZZ"
PO_BOX = re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+", re.IGNORECASE)
_WORD = re.compile(r"\b[a-z]{2,}\b")
_CAP_WORD = re.compile(r"\b[A-Z][a-z]+\b")
_LOWER_GREETING = re.compile(r"(?im)^(\s*(?:hi|hello|dear|hey|thanks|thx)[ ,]+)([a-z]{2,15})\b")
_AFTER_FIRST = re.compile(r"(\{\{[pq]:\d+:f\}\}\s+)([A-Z][a-z]+)\b")
_LOWER_SIGNOFF = re.compile(r"(?m)^(\s*-*\s*)([a-z]{2,15})(\.?\s*)$")
_LOWER_NAME = re.compile(r"(?<![\w'@./-])([a-z]{3,15})(?![\w'@-])")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ROOM = re.compile(r"\bEB ?(?=\d)")
_LOWER_PAIR = re.compile(r"\b([a-z]{2,20}) ([a-z]{2,20})\b")
BLOCK = frozenset(
    [
        "Im",
        "Ive",
        "Ill",
        "Id",
        "Youre",
        "Youve",
        "Youll",
        "Dont",
        "Cant",
        "Wont",
        "Isnt",
        "Hes",
        "Shes",
        "Theyre",
        "Weve",
        "Thats",
        "Whats",
        "Lets",
        "Its",
        "Didnt",
        "Doesnt",
        "Wouldnt",
        "Couldnt",
        "Shouldnt",
        "Havent",
        "Hasnt",
        "Arent",
        "Wasnt",
        "The",
        "This",
        "That",
        "Team",
        "Group",
        "Services",
        "Service",
        "Mailbox",
        "Announcements",
        "Announcement",
        "Department",
        "Office",
        "Calendar",
        "Desk",
        "Admin",
        "Administrator",
        "Support",
        "Help",
        "Info",
        "Payroll",
        "Benefits",
        "Center",
        "Corp",
        "North",
        "America",
        "American",
        "Global",
        "Energy",
        "Enron",
        "Corvane",
        "Outlook",
        "Migration",
        "Notes",
        "Lotus",
        "Email",
        "E-mail",
        "E-Mail",
        "Communications",
        "Public",
        "Relations",
        "Human",
        "Resources",
        "Resource",
        "Management",
        "Committee",
        "Board",
        "All",
        "Employees",
        "Employee",
        "Development",
        "Information",
        "Technology",
        "Systems",
        "Gas",
        "Power",
        "Trading",
        "Research",
        "Legal",
        "Finance",
        "Accounting",
        "Operations",
        "Logistics",
        "Security",
        "Network",
        "Newsletter",
        "News",
        "Daily",
        "Weekly",
        "Re",
        "Fw",
        "Fwd",
        "From",
        "To",
        "Cc",
        "Subject",
        "Sent",
        "Date",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Please",
        "Thanks",
        "Dear",
        "Hello",
        "Hi",
        "New",
        "York",
        "San",
        "Los",
        "Las",
        "Santa",
    ]
)


def date_like(run: str) -> bool:
    """A digit run that reads as a compact date (YYYYMMDD, optionally HHMM or HHMMSS)."""
    if len(run) not in (8, 12, 14) or run[:2] not in ("19", "20"):
        return False
    month, day = int(run[4:6]), int(run[6:8])
    return 1 <= month <= 12 and 1 <= day <= 31


def _placeish(token: str) -> bool:
    tok = token if not token.isupper() else token.capitalize()
    return tok in N.COMMON_WORDS or (tok in N.PLACES and tok not in N.COMMON_FIRST)


def _never_name(token: str) -> bool:
    """A capitalised token that is never a person's name (institution word, place)."""
    return _placeish(token) or token in N.NEVER_PERSON_ORGS or token in _MAPPED_WORDS


_MAPPED_WORDS = frozenset(
    w for v in (*N.ENTITY_MAP.values(), *N.THIRD_PARTY_MAP.values()) for w in v.split()
)


_DENY_TAG = b"deny\x1f"


def deny_entry(key: bytes, digest: str) -> str:
    """The `names.HASHED_DENY` entry for a token whose sha256(lower case) hex digest is
    `digest`: HMAC-SHA256 under the id key. Keyed over the digest (not the token) so the list
    is built from the committed digests alone (docs/leadership-inboxes.md, "Identifier key")."""
    return hmac.new(key, _DENY_TAG + digest.encode(), hashlib.sha256).hexdigest()


class DenyList:
    """`names.HASHED_DENY` under one id key. With any key other than the committed one nothing
    matches, which is why the CLI refuses other keys (`enron.load_key`)."""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.entries = N.HASHED_DENY
        self._seen: dict[str, bool] = {}  # lower-cased token -> listed (the scanner asks often)

    def matches(self, token: str) -> bool:
        low = token.lower()
        hit = self._seen.get(low)
        if hit is None:
            digest = hashlib.sha256(low.encode()).hexdigest()
            hit = self._seen[low] = deny_entry(self.key, digest) in self.entries
        return hit


def stable_hash(*parts: object) -> int:
    """Seeded, process-independent 64-bit hash (sha256) used for every deterministic choice."""
    data = "\x1f".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def title(token: str) -> str:
    return "-".join(p[:1].upper() + p[1:].lower() for p in token.split("-"))


_CONTRACTION = re.compile(r"'(?:m|ve|ll|d|re|t)$|n't$", re.IGNORECASE)


def _alpha_name(token: str) -> bool:
    if "'" in token and not re.fullmatch(r"[A-Z]'[A-Z][a-z]+", token):
        return False  # only O'Brien-style apostrophes
    return (
        2 <= len(token) <= 20 and re.fullmatch(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]", token) is not None
    )


# ------------------------------------------------------------------------------ registry


@dataclass
class Person:
    first: str
    last: str
    internal: bool = False  # seen with a company address (Lotus Notes or @enron.com)
    domain: str | None = None  # an external email domain the person was seen with


@dataclass
class Registry:
    """Real people (harvested) and the corpus' lower-case vocabulary. Never written into
    committed output; `real_names()` feeds the scanner and the pseudonym allocator."""

    people: list[Person] = field(default_factory=list)
    index: dict[tuple[str, str], int] = field(default_factory=dict)
    by_first: dict[str, list[int]] = field(default_factory=dict)
    by_last: dict[str, list[int]] = field(default_factory=dict)
    vocab: frozenset[str] = frozenset()  # ordinary words (mostly written in lower case)
    lexicon: frozenset[str] = frozenset()  # every word seen in lower case often enough

    def lookup(self, first: str, last: str) -> int | None:
        return self.index.get((first.lower(), last.lower()))

    def add(
        self, first: str, last: str, *, internal: bool = False, domain: str | None = None
    ) -> int:
        key = (first.lower(), last.lower())
        pid = self.index.get(key)
        if pid is None:
            pid = len(self.people)
            self.people.append(Person(title(first), title(last)))
            self.index[key] = pid
            self.by_first.setdefault(key[0], []).append(pid)
            self.by_last.setdefault(key[1], []).append(pid)
        person = self.people[pid]
        person.internal = person.internal or internal
        if domain and person.domain is None and not internal:
            person.domain = domain
        return pid

    def is_word(self, token: str) -> bool:
        return token.lower() in self.vocab

    def real_names(self) -> list[str]:
        return sorted({f"{p.first} {p.last}" for p in self.people})


class Harvester:
    """Collects name candidates and lower-case vocabulary from raw texts."""

    def __init__(self) -> None:
        self.words: Counter[str] = Counter()
        self.caps: Counter[str] = Counter()
        self.candidates: Counter[tuple[str, str]] = Counter()
        self.internal: set[tuple[str, str]] = set()
        self.domains: dict[tuple[str, str], str] = {}

    def _cand(self, first: str, last: str, *, internal: bool = False, domain: str | None = None,
              weight: int = 1) -> None:  # fmt: skip
        if not (_alpha_name(first) and _alpha_name(last)):
            return
        key = (title(first), title(last))
        self.candidates[key] += weight
        if internal:
            self.internal.add(key)
        elif domain:
            self.domains.setdefault(key, domain)

    def _person(self, text: str, **kw: object) -> None:
        parts = text.split()
        if len(parts) >= 2:
            self._cand(parts[0], parts[-1], **kw)  # type: ignore[arg-type]

    def feed(self, text: str) -> None:
        stripped = URL.sub(" ", EMAIL.sub(" ", text))
        self.words.update(_WORD.findall(stripped))
        self.caps.update(_CAP_WORD.findall(stripped))
        for m in LOTUS.finditer(text):
            if m.group(2) in N.LOTUS_UNITS or m.group(3) or m.group(4):
                self._person(m.group(1), internal=True)
        for m in LOTUS_AT.finditer(text):
            self._person(m.group(1), internal=True)
        for m in EMAIL.finditer(text):
            local, _, domain = m.group(0).partition("@")
            bits = [b for b in re.split(r"[._]", local) if b]
            if len(bits) in (2, 3) and all(b.isalpha() for b in bits):
                internal = "enron" in domain.lower()
                self._cand(bits[0], bits[-1], internal=internal, domain=domain.lower())
        for m in _DISPLAY.finditer(text):
            name = m.group(1).strip().strip('"').strip()
            if "," in name:
                last, _, first = name.partition(",")
                self._person(f"{first.strip()} {last.strip()}")
            elif name[:1].isupper():
                self._person(name)
        for line in text.split("\n"):
            fm = re.match(r"^\s*(From|To|Cc|cc|Sent by)\s*:\s*(.+)$", line)
            if not fm:
                continue
            for part in fm.group(2).split(";"):
                lf = re.fullmatch(rf"\s*\"?({_NAME_RX}), ({_NAME_RX})(?: [A-Z]\.?)?\"?\s*", part)
                if lf:
                    self._cand(lf.group(2), lf.group(1))

    def finish(self, *, min_vocab: int = 3, ratio: float = 0.25) -> Registry:
        """An ordinary word appears in lower case at least `min_vocab` times and at least
        `ratio` times as often as capitalised (lower-case signatures do not make names words)."""
        vocab = frozenset(
            w
            for w, c in self.words.items()
            if c >= min_vocab and c >= ratio * self.caps.get(w.capitalize(), 0)
        )
        lexicon = frozenset(w for w, c in self.words.items() if c >= min_vocab)
        reg = Registry(vocab=vocab, lexicon=lexicon)
        seeds = [tuple(n.split()) for n in N.KNOWN_PEOPLE]
        cands = dict(self.candidates)
        for parts in seeds:
            cands.setdefault((parts[0], parts[-1]), 1)
        known = {(parts[0], parts[-1]) for parts in seeds}
        for first, last in sorted(cands, key=lambda k: (k[1], k[0])):
            seed = (first, last) in known  # known people are always registered
            if not seed and not self._valid(first, last, vocab):
                continue
            key = (first, last)
            reg.add(first, last, internal=key in self.internal, domain=self.domains.get(key))
        return reg

    @staticmethod
    def _valid(first: str, last: str, vocab: frozenset[str]) -> bool:
        if first in BLOCK or last in BLOCK or first.lower() == last.lower():
            return False
        if _never_name(first) or (_never_name(last) and _placeish(first)):
            return False  # "Federal Credit", "Global Business", "Corpus Christi"
        if first in N.COMMON_FIRST:
            return True
        return first.lower() not in vocab and last.lower() not in vocab


# --------------------------------------------------------------------------- template


@dataclass
class Template:
    text: str
    refs: list[str]  # placeholder refs in order of first appearance ("p:12", "x:3", ...)


class Pseudonymizer:
    """Rewrites raw text into a template (see module docstring). Stateful only in the ids it
    allocates for first-name-only, surname-only and external-address mentions (allocated in
    processing order, which is deterministic)."""

    def __init__(self, registry: Registry, seed: int, id_key: bytes) -> None:
        """`id_key` keys the hash of identifiers (deal / notice / contract numbers) and the deny
        list: the committed key (`enron.DEFAULT_ID_KEY`, see `enron.load_key`); tests pass their
        own."""
        if len(id_key) < 16:
            raise ValueError("an id key of at least 16 bytes is required (enron.DEFAULT_ID_KEY)")
        self.reg = registry
        self.seed = seed
        self.id_key = id_key
        self.deny = DenyList(id_key)
        self.emitted: set[str] = set()  # hashed digit runs (6+ digits) written so far
        self.first_ids: dict[str, int] = {}
        self.last_ids: dict[str, int] = {}
        self.emails: dict[str, int] = {}
        self.org_ids: dict[str, int] = {}
        self.place_ids: dict[str, int] = {}
        self.email_domains: list[str] = []
        self.dynamic: set[int] = set()

    # -- id allocation ---------------------------------------------------------------
    def _alloc(self, table: dict[str, int], key: str) -> int:
        if key not in table:
            table[key] = len(table)
        return table[key]

    def _email_ref(self, address: str) -> str:
        local, _, domain = address.partition("@")
        bits = [b for b in re.split(r"[._]", local) if b]
        if len(bits) in (2, 3) and all(b.isalpha() for b in bits):
            pid = self.reg.lookup(bits[0], bits[-1])
            if pid is not None:
                return f"p:{pid}"
        key = address.lower()
        if key not in self.emails:
            self.emails[key] = len(self.emails)
            self.email_domains.append(domain.lower())
        return f"x:{self.emails[key]}"

    # -- passes ----------------------------------------------------------------------
    def _person_ref(self, full: str, local: set[int]) -> str | None:
        parts = full.split()
        pid = self.reg.lookup(parts[0], parts[-1])
        if pid is None:
            return None
        local.add(pid)
        return f"p:{pid}"

    def _fake_url(self, m: re.Match[str]) -> str:
        url = m.group(0)
        host = re.sub(r"^(?:https?://)?", "", url, flags=re.IGNORECASE).split("/")[0].lower()
        if "enron" in host:
            return f"https://intranet.{N.COMPANY_DOMAIN}/"
        org = N.FAKE_ORGS[stable_hash(self.seed, "org", host) % len(N.FAKE_ORGS)]
        return f"https://www.{org}.example.test/"

    def _fake_host(self, m: re.Match[str]) -> str:
        tld = m.group(1).rsplit(".", 1)[-1]
        if tld[:1].isupper() and not tld.isupper():
            return m.group(0)  # "that.In the": a missing space, not a host
        host = m.group(1).lower()
        if host.endswith("example.test"):
            return m.group(0)  # pragma: no cover - the pattern never matches .test
        if "enron" in host:
            return N.COMPANY_DOMAIN
        return f"{N.FAKE_ORGS[stable_hash(self.seed, 'org', host) % len(N.FAKE_ORGS)]}.example.test"

    def _lotus(self, m: re.Match[str], local: set[int]) -> str:
        groups = m.groups()
        if m.re is LOTUS and not (groups[1] in N.LOTUS_UNITS or groups[2] or groups[3]):
            return m.group(0)
        ref = self._person_ref(groups[0], local)
        return f"{{{{{ref}:e}}}}" if ref else groups[0]

    def _display(self, m: re.Match[str], local: set[int]) -> str:
        name = m.group(1).strip().strip('"').strip()
        if "," in name:
            last, _, first = name.partition(",")
            name = f"{first.strip()} {last.strip()}"
        ref = self._person_ref(name, local) if len(name.split()) >= 2 else None
        if ref is None:
            return m.group(0)
        return f"{{{{{ref}:n}}}} <{{{{{ref}:e}}}}>"

    def _last_first(self, m: re.Match[str], local: set[int]) -> str:
        pid = self.reg.lookup(m.group(2), m.group(1))
        if pid is None:
            return m.group(0)
        local.add(pid)
        return f"{{{{p:{pid}:n}}}}"

    def _titled(self, m: re.Match[str], local: set[int]) -> str:
        t, a, b = m.group(1), m.group(2), m.group(3)
        if b:
            pid = self.reg.lookup(a, b)
            if pid is not None:
                local.add(pid)
                return f"{t}. {{{{p:{pid}:l}}}}"
        surname = b or a
        pids = self.reg.by_last.get(surname.lower(), [])
        chosen = next((p for p in pids if p in local), pids[0] if pids else None)
        if chosen is not None:
            return f"{t}. {{{{p:{chosen}:l}}}}"
        return f"{t}. {{{{s:{self._alloc(self.last_ids, surname)}:l}}}}"

    def _full(self, m: re.Match[str], local: set[int]) -> str:
        first, last = m.group(1), m.group(3)
        if _never_name(first) and (_never_name(last) or last in BLOCK):
            return m.group(0)  # "Kansas City", "Pacific Gas": never a person
        pid = self.reg.lookup(first, last)
        if pid is None and self._new_name(first, last):
            pid = self.reg.add(first, last)
            self.dynamic.add(pid)
        if pid is None:
            return m.group(0)
        local.add(pid)
        return f"{{{{p:{pid}:n}}}}"

    def _intro(self, m: re.Match[str], local: set[int]) -> str:
        """An introduced name ("This is Li Sun"): always a person, even short or word-like."""
        words = m.group(1).split()
        if any(w in N.PROPER_ALLOW or w in N.CONTEXT_STOP or w in BLOCK for w in words[:1]):
            return m.group(0)
        lead = m.group(0)[: m.start(1) - m.start()].lower()
        if len(words) == 1 and "name" not in lead:
            word = words[0].lower()
            if (
                lead.startswith(("i am", "i'm"))
                or self.reg.is_word(word)
                or word in self.reg.lexicon
            ):
                return m.group(0)  # "This is Great", "I am Glad": ordinary words
        if len(words) == 2:
            pid = self.reg.lookup(words[0], words[1])
            if pid is None:
                pid = self.reg.add(words[0], words[1])
                self.dynamic.add(pid)
            local.add(pid)
            repl = f"{{{{p:{pid}:n}}}}"
        else:
            repl = f"{{{{q:{self._alloc(self.first_ids, words[0])}:f}}}}"
        return m.group(0)[: m.start(1) - m.start()] + repl

    def _surname_after_first(self, m: re.Match[str], local: set[int]) -> str:
        """``<first-name placeholder> Germany``: a registered surname right after a first name is
        a surname, even when it is also an allowed proper noun (a country, a state)."""
        tok = m.group(2)
        pids = self.reg.by_last.get(tok.lower(), [])
        if not pids:
            return m.group(0)
        chosen = next((p for p in pids if p in local), pids[0])
        return f"{m.group(1)}{{{{p:{chosen}:l}}}}"

    def _lower_greeting(self, m: re.Match[str]) -> str:
        name = m.group(2)
        if name.capitalize() not in N.COMMON_FIRST and name not in self.reg.by_first:
            return m.group(0)
        if self.reg.is_word(name) and name.capitalize() not in N.COMMON_FIRST:
            return m.group(0)
        return f"{m.group(1)}{{{{q:{self._alloc(self.first_ids, name.capitalize())}:f}}}}"

    def _lower_name(self, m: re.Match[str], local: set[int]) -> str:
        """Lower-case first names ("talk to louise") and distinctive registered surnames
        ("ask lavorato") that are not ordinary words of the corpus."""
        tok = m.group(1)
        if (
            self.reg.is_word(tok)
            or tok in self.reg.lexicon
            and tok.capitalize() not in N.COMMON_FIRST
        ):
            return tok
        cap = tok.capitalize()
        if cap in N.AMBIGUOUS_FIRST or cap in N.CONTEXT_STOP or cap in N.PROPER_ALLOW:
            return tok
        lasts = self.reg.by_last.get(tok, [])
        if lasts and tok not in self.reg.by_first and cap not in N.COMMON_FIRST:
            chosen = next((p for p in lasts if p in local), lasts[0])
            return f"{{{{p:{chosen}:l}}}}"
        if cap in N.COMMON_FIRST:
            pids = [p for p in self.reg.by_first.get(tok, []) if p in local]
            if pids:
                return f"{{{{p:{pids[0]}:f}}}}"
            return f"{{{{q:{self._alloc(self.first_ids, cap)}:f}}}}"
        return tok

    def _lower_pair(self, m: re.Match[str], local: set[int]) -> str:
        first, last = m.group(1), m.group(2)
        if self.reg.is_word(first) or self.reg.is_word(last):
            return m.group(0)
        pid = self.reg.lookup(first, last)
        if pid is None:
            return m.group(0)
        local.add(pid)
        return f"{{{{p:{pid}:n}}}}"

    def _new_name(self, first: str, last: str) -> bool:
        """An unregistered "First Last" pair that is very likely a person: a common first name
        or two capitalised tokens that are not ordinary words, allowed nouns or acronyms."""
        ok = [
            _alpha_name(t)
            and t not in BLOCK
            and not _never_name(t)
            and t not in N.PROPER_ALLOW
            and not t.isupper()
            and t not in N.CONTEXT_STOP
            for t in (first, last)
        ]
        if not all(ok):
            return False
        if first in N.COMMON_FIRST and first not in N.AMBIGUOUS_FIRST:
            return True
        return not self.reg.is_word(first) and not self.reg.is_word(last)

    def _context(self, text: str, start: int, end: int) -> bool:
        """Greeting or sign-off position: the token is (nearly) alone on its line, or follows
        a greeting/sign-off word."""
        ls = text.rfind("\n", 0, start) + 1
        le = text.find("\n", end)
        le = len(text) if le == -1 else le
        before, after = text[ls:start], text[end:le]
        if re.fullmatch(rf"\s*(?:-+\s*|{_GREETING_WORDS},?\s+)?", before) and re.fullmatch(
            r"\s*[,:!.;-]?\s*(?:-.*)?", after
        ):
            return True
        if re.search(r"\s[-:]\s*$", before) and not after.strip(" .,"):
            return True  # "30 Minutes - Bill": a name closing a list line
        return bool(_SIGNOFF_WORDS.search(before.rstrip() + " ") or re.search(
            rf"{_GREETING_WORDS},?\s+$", before
        ))  # fmt: skip

    @staticmethod
    def _addressed(text: str, start: int, end: int) -> bool:
        """``Bill: I am resending ...``, ``[Bill, can you ...]`` - a first name opening a line
        (or a bracket), then ':' or ','."""
        ls = text.rfind("\n", 0, start) + 1
        lead = text[ls:start].strip()
        if lead and not lead.endswith(("[", "(")):
            return False
        return re.match(r"\s*[,:]\s", text[end : end + 3]) is not None

    def _lower_signoff(self, m: re.Match[str]) -> str:
        name = m.group(2)
        if name.capitalize() not in N.COMMON_FIRST or name.capitalize() in N.AMBIGUOUS_FIRST:
            return m.group(0)
        return (
            f"{m.group(1)}{{{{q:{self._alloc(self.first_ids, name.capitalize())}:f}}}}{m.group(3)}"
        )

    def _single(self, m: re.Match[str], text: str, local: set[int]) -> str:
        whole = m.group(0)
        if _CONTRACTION.search(whole):
            return whole  # I'm, I've, I'll, We'd, Don't: never a name
        tok, suffix = whole, ""
        if whole.endswith(("'s", "'S")):
            tok, suffix = whole[:-2], whole[-2:]
        elif whole.endswith("'"):
            tok, suffix = whole[:-1], "'"
        out = self._single_token(tok, m, text, local, possessive=suffix == "'s")
        return whole if out == tok else out + suffix

    def _single_token(self, tok: str, m: re.Match[str], text: str, local: set[int],
                      possessive: bool = False) -> str:  # fmt: skip
        if self.deny.matches(tok):
            return f"{{{{q:{self._alloc(self.first_ids, tok)}:f}}}}"
        if tok in N.PROPER_ALLOW or tok in BLOCK or _never_name(tok):
            return tok
        if tok.isupper() and len(tok) > 2 and self._shouting(text, m.start()):
            return tok  # "MARK YOUR CALENDARS": a word in an all-caps line
        if self._in_title_phrase(text, m.start()):
            return tok  # "Rate and Currency Risk Desk", "Direct Access": not a person
        low = tok.lower()
        word = self.reg.is_word(tok)
        lasts = self.reg.by_last.get(low, [])
        if lasts and not word and len(tok) >= 3 and low not in self.reg.by_first:
            chosen = next((p for p in lasts if p in local), lasts[0])
            return f"{{{{p:{chosen}:l}}}}"
        if tok in N.CONTEXT_STOP:
            return tok
        if tok in N.COMMON_FIRST:
            word = tok in N.AMBIGUOUS_FIRST
        known_first = low in self.reg.by_first or tok in N.COMMON_FIRST
        in_context = self._context(text, m.start(), m.end()) or (
            possessive and tok in N.AMBIGUOUS_FIRST  # "Bill's reaction": a person
        )
        if known_first and not in_context and (not word or tok in N.COMMON_FIRST):
            in_context = self._addressed(text, m.start(), m.end())  # not "Where: CT 52"
        if known_first and (not word or in_context):
            pids = [p for p in self.reg.by_first.get(low, []) if p in local]
            if pids:
                return f"{{{{p:{pids[0]}:f}}}}"
            return f"{{{{q:{self._alloc(self.first_ids, tok)}:f}}}}"
        if in_context and not tok.isupper() and _alpha_name(tok):
            return f"{{{{q:{self._alloc(self.first_ids, tok)}:f}}}}"
        return tok

    @staticmethod
    def _shouting(text: str, start: int) -> bool:
        ls = text.rfind("\n", 0, start) + 1
        le = text.find("\n", start)
        line = text[ls : len(text) if le == -1 else le]
        return len(re.findall(r"\b[A-Z]{2,}\b", line)) >= 3

    def _in_title_phrase(self, text: str, start: int) -> bool:
        """The token follows an institution word mid-sentence ("Global Business X")."""
        before = text[max(0, start - 40) : start]
        prev = re.search(r"(?:^|[^\n.!?:;])\s([A-Z][a-z]+) $", before)
        if not prev or before.rstrip().endswith((".", "!", "?", ":")):
            return False
        word = prev.group(1)
        line_start = text.rfind("\n", 0, start) + 1
        if text[line_start:start].strip() == word:
            return False  # "Direct X" opening a line: a greeting-like or heading position
        return word in N.COMMON_WORDS

    def _keyed(self, kind: str, value: str) -> int:
        mac = hmac.new(self.id_key, f"{kind}\x1f{value}".encode(), hashlib.sha256).digest()
        return int.from_bytes(mac[:8], "big")

    def _hold(self, value: str) -> str:
        """A digit-free sentinel for an already hashed value (restored at the end of `_digits`,
        so later digit rules never hash a hashed id again)."""
        self._held.append(value)
        n, tag = len(self._held) - 1, ""
        while True:
            n, r = divmod(n, 26)
            tag = chr(97 + r) + tag
            if not n:
                break
        return f"\x00{tag}\x01"

    def _release(self, text: str) -> str:
        def back(m: re.Match[str]) -> str:
            n = 0
            for ch in m.group(1):
                n = n * 26 + ord(ch) - 97
            return self._held[n]

        return re.sub(r"\x00([a-z]+)\x01", back, text)

    def _hash_digits(self, digits: str) -> str:
        """Keyed same-length replacement for a deal / contract / reference number."""
        n = len(digits)
        out = f"{self._keyed('id', digits) % 10**n:0{n}d}"
        if n >= 6:
            self.emitted.add(out)
        return self._hold(out)

    def _any_digits(self, m: re.Match[str]) -> str:
        run = m.group(0)
        return run if date_like(run) else self._hash_digits(run)

    def _alnum_id(self, m: re.Match[str]) -> str:
        digits = f"{self._keyed('alnum', m.group(0)) % 10**7:07d}"
        self.emitted.add(digits)
        return self._hold(f"CV-{digits}")

    def _digits(self, text: str) -> str:
        def phone(m: re.Match[str]) -> str:
            n = stable_hash(self.seed, "tel", m.group(0)) % 100
            raw = m.group(0)
            if raw.lower().startswith(("x", "ext")):
                return f"x55{n:02d}"
            if len(re.sub(r"\D", "", raw)) <= 7:
                return f"555-01{n:02d}"
            return f"(555) 555-01{n:02d}"

        self._held: list[str] = []
        text = GLUED_PHONE.sub(lambda m: m.group(0) + " ", text)
        text = SSN.sub("000-00-0000", text)
        text = ROOM.sub("CT ", text)
        text = ALNUM_ID.sub(self._alnum_id, text)
        text = ZIP_HQ.sub(_fake_zip, text)
        text = PO_BOX.sub("P.O. Box 100", text)
        text = INTL_PHONE.sub(phone, text)
        text = PHONE_RUN.sub(lambda m: phone(m) if is_phone(m.group(0)) else m.group(0), text)
        text = PHONE_SHORT.sub(phone, text)
        text = DEAL_NO.sub(lambda m: m.group(1) + self._hash_digits(m.group(2)), text)
        text = HASH_ID.sub(lambda m: m.group(1) + self._hash_digits(m.group(2)), text)
        text = PASSCODE.sub(lambda m: m.group(1) + self._hash_digits(m.group(2)), text)
        text = ANY_DIGITS.sub(self._any_digits, text)
        text = IPV4.sub(
            lambda m: f"192.0.2.{stable_hash(self.seed, 'ip', m.group(0)) % 250 + 1}", text
        )
        text = DEAL_ALNUM.sub(lambda m: f"CX{self._hash_digits(m.group(1))}{m.group(2)}", text)
        text = UK_POSTCODE.sub(FAKE_POSTCODE, text)
        return self._release(STREET.sub(N.FAKE_STREET, text))

    @staticmethod
    def _entities(text: str) -> str:
        table = {**N.ENTITY_MAP, **N.THIRD_PARTY_MAP}
        for key in sorted(table, key=lambda k: (-len(k), k)):
            value = table[key]
            if key.islower():
                text = re.sub(re.escape(key), value, text, flags=re.IGNORECASE)
            elif key in N.ENTITY_NAMES:  # distinctive names: any case ("dynegy", "DYNEGY")
                text = re.sub(rf"(?<![A-Za-z]){re.escape(key)}(?![A-Za-z])", value, text,
                              flags=re.IGNORECASE)  # fmt: skip
            else:
                text = re.sub(rf"(?<![A-Za-z]){re.escape(key)}(?![a-z])", value, text)
        return STRICT_ACRONYM.sub(lambda m: N.STRICT_ACRONYMS[m.group(0)], text)

    def _org(self, m: re.Match[str]) -> str:
        """``<distinctive words> [generic words] <suffix>``: the distinctive words of a firm's
        name become an organisation placeholder ("Blue Flame Propane Inc." -> ``{{o:3:n}}
        Propane Inc.``); Corvane, mapped and public names stay."""
        words = m.group("words").split()
        tail: list[str] = []
        while words and (words[-1] in N.COMMON_WORDS or words[-1] in ("&", "and", "of")):
            tail.insert(0, words.pop())
        head = [w for w in words if w not in ("The", "the")]
        if not head or all(_known_org_word(w) for w in head):
            return m.group(0)
        key = " ".join(head).lower()
        oid = self._alloc(self.org_ids, key)
        pre = "The " if words and words[0] in ("The", "the") else ""
        rest = (" " + " ".join(tail)) if tail else ""
        return f"{pre}{{{{o:{oid}:n}}}}{rest}{m.group('gap')}{m.group('suffix')}"

    def _place_candidate(self, tok: str) -> bool:
        """A token that the name passes would take for a person: not an ordinary word, an
        allowed proper noun, a real place of the PLACES list, a month or a direction."""
        return not (
            tok in BLOCK
            or tok in _NOT_PLACE
            or _never_name(tok)
            or tok in N.PROPER_ALLOW
            or tok in N.CONTEXT_STOP
            or self.reg.is_word(tok)
            or tok.isupper()
        )

    def _place_ref(self, tok: str) -> str:
        return f"{{{{l:{self._alloc(self.place_ids, tok.lower())}:n}}}}"

    def _places(self, text: str) -> str:
        def one(m: re.Match[str], group: int = 1) -> str:
            tok = m.group(group)
            if tok is None or not self._place_candidate(tok):
                return m.group(0)
            a, b = m.start(group) - m.start(), m.end(group) - m.start()
            return m.group(0)[:a] + self._place_ref(tok) + m.group(0)[b:]

        def route(m: re.Match[str]) -> str:
            return one(m, 1 if m.group(1) else 2)

        def at(m: re.Match[str]) -> str:
            refs = {g: self._place_ref(m.group(g)) for g in (1, 2)
                    if m.group(g) is not None and self._place_candidate(m.group(g))}  # fmt: skip
            out = m.group(0)
            for group in sorted(refs, reverse=True):  # right to left: earlier offsets stay valid
                a, b = m.start(group) - m.start(), m.end(group) - m.start()
                out = out[:a] + refs[group] + out[b:]
            return out

        text = PLACE_BEFORE_ASSET.sub(one, text)
        text = PLACE_AFTER_THE.sub(one, text)
        text = PLACE_ROUTE.sub(route, text)
        paragraphs = re.split(r"(\n\s*\n)", text)
        for i, para in enumerate(paragraphs):
            if _SITE_WORDS.search(para):
                paragraphs[i] = PLACE_AT.sub(at, para)
        return "".join(paragraphs)

    def apply(self, text: str, local: set[int] | None = None) -> Template:
        """Template for one text. `local` collects the person ids mentioned in full (shared
        across the segments of one message so first names resolve to the same person)."""
        local = set() if local is None else local
        text = text.replace("{{", "{ {").replace("}}", "} }")
        text = GLUED_PHONE.sub(lambda m: m.group(0) + " ", text)  # "Tel.713-..." before names
        text = re.sub(r"mailto:", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<=[A-Za-z0-9]) @ ?(?=[A-Za-z0-9\-]+\.[A-Za-z])", "@", text)  # "x @y.com"
        text = _DISPLAY.sub(lambda m: self._display(m, local), text)
        text = EMAIL.sub(lambda m: f"{{{{{self._email_ref(m.group(0))}:e}}}}", text)
        text = LOTUS.sub(lambda m: self._lotus(m, local), text)
        text = LOTUS_AT.sub(lambda m: self._lotus(m, local), text)
        text = URL.sub(self._fake_url, text)
        text = HOST.sub(self._fake_host, text)
        text = STREET.sub(N.FAKE_STREET, text)  # before names: "1221 N. Lamar Blvd."
        text = self._entities(text)
        text = re.sub(r"\bcorvane\.(?:com|net|org)\b", N.COMPANY_DOMAIN, text, flags=re.IGNORECASE)
        text = ORG_NAME.sub(self._org, text)
        text = self._places(text)
        text = _LAST_FIRST.sub(lambda m: self._last_first(m, local), text)
        text = _TITLED.sub(lambda m: self._titled(m, local), text)
        text = _INTRO.sub(lambda m: self._intro(m, local), text)
        text = _FULL.sub(lambda m: self._full(m, local), text)
        text = _LOWER_PAIR.sub(lambda m: self._lower_pair(m, local), text)
        text = _LOWER_NAME.sub(lambda m: self._lower_name(m, local), text)
        text = _SINGLE.sub(lambda m: self._single(m, text, local), text)
        text = _AFTER_FIRST.sub(lambda m: self._surname_after_first(m, local), text)
        text = _LOWER_GREETING.sub(self._lower_greeting, text)
        text = _LOWER_SIGNOFF.sub(self._lower_signoff, text)
        text = self._digits(text)
        return Template(text, refs_in(text))

    def parties(self, field_value: str, local: set[int]) -> list[str]:
        """Refs of the people/addresses in a quoted header field (From/To/Cc)."""
        if not field_value.strip():
            return []
        return refs_in(self.apply(field_value, local).text)


def refs_in(text: str) -> list[str]:
    out: list[str] = []
    for kind, num, _ in PLACEHOLDER.findall(text):
        ref = f"{kind}:{num}"
        if ref not in out:
            out.append(ref)
    return out


def unknown_proper_nouns(text: str, registry: Registry, deny: DenyList | None = None
                         ) -> list[str]:  # fmt: skip
    """Capitalised tokens left in a template that are neither ordinary corpus words, allowed
    proper nouns nor short acronyms: candidates for undetected names (strict curation). With
    `deny`, deny-listed tokens are always candidates."""
    bare = PLACEHOLDER.sub(" ", text)
    out = []
    for tok in re.findall(r"\b[A-Z][a-zA-Z]+\b", bare):
        low = tok.lower()
        ordinary = registry.is_word(tok) or (
            low in registry.lexicon and low not in registry.by_first and low not in registry.by_last
        )
        if tok in N.PROPER_ALLOW or tok in N.CONTEXT_STOP or ordinary or _never_name(tok):
            continue
        if tok.isupper() and len(tok) <= 4:
            continue
        out.append(tok)
    if deny is not None:
        out += [t for t in re.findall(r"\b[A-Z][a-zA-Z]+\b", bare) if deny.matches(t)]
    # charset-mangled names ("G_nther", "Fran?ois") can never be checked against a vocabulary
    out += re.findall(r"\b[A-Z][a-z]*[_?][a-z]+\b", bare)
    return sorted(set(out))


# ------------------------------------------------------------------- places and assets
# A capitalised token in a place / asset position is a place, never a person: before an asset
# noun ("X Power Pool", "the X Trader", "X Plant"), after "at/from/to/near/in the" ("at the X"),
# on a route with a trading hub ("from X to NP-15") or after "at"/"both" in a paragraph about
# plants and sites ("site rep for both X and Y"). Such tokens become ``{{l:N:n}}``.
_PLACE_WORD = r"[A-Z][a-z]{2,}(?:-[A-Z][a-z]+)?"
_ASSET_TAIL = (
    r"(?:Power Pool|Pool|Plant|Generating Station|Station|Units?|Hub|Trader|Interconnect|"
    r"Lateral|Substation|Refinery|Terminal|Storage|Field|Site|Facility|Project|Hotel|Inn|Resort|"
    r"Lodge|Country Club|Dam|Mine|Farm|City Gate|Compressor|Delivery Point|Receipt Point)"
)
PLACE_BEFORE_ASSET = re.compile(rf"\b({_PLACE_WORD})(?=[ \t]+{_ASSET_TAIL}\b)")
PLACE_AFTER_THE = re.compile(
    rf"\b(?:[Aa]t|[Ff]rom|[Tt]o|[Nn]ear|[Ii]n|[Bb]y|[Oo]utside) the ({_PLACE_WORD})\b(?![ \t]+[A-Z])"
)
HUBS = (
    r"(?:NP-?15|SP-?15|ZP-?26|COB|Mid-?C|Malin|Palo Verde|PV|Henry Hub|Four Corners|Mead|"
    r"Cinergy|ComEd|Entergy|TVA|PJM|NEPOOL|ERCOT|SoCal|PG&E Citygate|Chicago|Waha|Katy)"
)
PLACE_ROUTE = re.compile(
    rf"\b[Ff]rom ({_PLACE_WORD}) to (?={HUBS}\b)|\b[Ff]rom {HUBS} to ({_PLACE_WORD})\b"
)
_SITE_WORDS = re.compile(
    r"\b(?:sites?|plants?|units?|facilit(?:y|ies)|turbines?|peakers?|MW|generators?|"
    r"mobilization|substations?|interconnects?)\b",
    re.IGNORECASE,
)
PLACE_AT = re.compile(
    rf"\b(?:at|both|for both)[ \t]+({_PLACE_WORD})(?:\?|\b)(?![ \t]+[A-Z])"
    rf"(?:[ \t]+and[ \t]+({_PLACE_WORD})\b(?![ \t]+[A-Z]))?"
)
_NOT_PLACE = frozenset(
    ["West", "East", "South", "North", "Northeast", "Northwest", "Southeast", "Southwest",
     "Midwest", "Coast", "Bay", "Valley", "Mountain", "River", "Lake", "Mid"]
)  # fmt: skip

_ORG_WORD = r"[A-Z][A-Za-z&'\-]*\.?"
ORG_NAME = re.compile(
    rf"(?P<words>(?<![\w.@/{{}}-]){_ORG_WORD}(?:[ \t]+(?:(?:&|and|of)[ \t]+)?{_ORG_WORD}){{0,4}}?)"
    r"(?P<gap>,?[ \t]+)(?P<suffix>Inc\b\.?|Incorporated\b|Corp\b\.?|Corporation\b|LLC\b|L\.L\.C\.|"
    r"L\.P\.|LP\b|Ltd\b\.?|Limited\b|Company\b|Partners\b|Group\b|Holdings\b)"
)


def _known_org_word(word: str) -> bool:
    """A word of a firm's name that can stay: the company, mapped names, public organisations,
    institution words, persona-domain names."""
    w = word.rstrip(".,")
    return (
        w in N.COMMON_WORDS
        or w in N.NEVER_PERSON_ORGS
        or w in _MAPPED_WORDS
        or w in BLOCK
        or w in N.PROPER_ALLOW
        or _placeish(w)
        or w.lower() in ("the", "and", "of", "&")
    )


ORG = re.compile(
    r"((?:\b[A-Z][A-Za-z&'.-]*\s+){1,3})(?:Media|Inc\b\.?|Incorporated|Corp\b\.?|Corporation|LLC|"
    r"Ltd\b\.?|Limited|Company|Co\.|Group|Partners|Associates|Systems|Software|Technologies|"
    r"Solutions|Consulting|Capital|Bank|Securities|Holdings|Ventures|Labs|Networks|"
    r"Communications|Publishing|Research|Advisors|Advisory|Enterprises)\b"
)


def unknown_organisations(text: str, registry: Registry) -> list[str]:
    """Organisation names ("Questia Media", "Acme Software Inc") whose distinctive words are
    neither ordinary corpus words nor allowed/public names: possibly identifying private firms."""
    bare = PLACEHOLDER.sub(" ", text)
    out = []
    for m in ORG.finditer(bare):
        words = m.group(1).split()
        odd = [
            w for w in words
            if not (registry.is_word(w) or w in N.PROPER_ALLOW or w in N.NEVER_PERSON_ORGS
                    or w.rstrip(".,").lower() in {"the", "and", "of", "&"})
        ]  # fmt: skip
        if odd:
            out.append(m.group(0).strip())
    return sorted(set(out))


# ---------------------------------------------------------------------------- render


@dataclass(frozen=True)
class Identity:
    first: str
    last: str
    email: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}".strip()


def fake_identity(seed: int, kind: str, num: int, *, real: frozenset[str], reserved: frozenset[str],
                  internal: bool, org: int | None) -> Identity:  # fmt: skip
    """Deterministic pseudonym for one placeholder id (never a real or reserved full name)."""
    salt = 0
    while True:
        first = N.FAKE_FIRST[stable_hash(seed, kind, num, "f", salt) % len(N.FAKE_FIRST)]
        last = N.FAKE_LAST[stable_hash(seed, kind, num, "l", salt) % len(N.FAKE_LAST)]
        full = f"{first} {last}".lower()
        if full not in real and full not in reserved:
            break
        salt += 1
    if internal:
        domain = N.COMPANY_DOMAIN
    else:
        index = org if org is not None else stable_hash(seed, kind, num, "org") % len(N.FAKE_ORGS)
        domain = f"{N.FAKE_ORGS[index % len(N.FAKE_ORGS)]}.example.test"
    local = f"{first}.{last}" if kind == "p" else f"{first[0]}{last}"
    return Identity(first, last, f"{local.lower()}@{domain}")


@dataclass
class Renderer:
    """Renders templates. `people`/`externals` carry per-id metadata from the import
    (`internal`, `org`); `real` are lower-cased real full names that pseudonyms must avoid."""

    seed: int
    people: dict[int, dict[str, object]] = field(default_factory=dict)
    externals: dict[int, dict[str, object]] = field(default_factory=dict)
    real: frozenset[str] = frozenset()
    reserved: frozenset[str] = frozenset()
    # per-scope (one scenario) assignments: two placeholder ids never share a full name
    _assigned: dict[str, Identity] = field(default_factory=dict, init=False, repr=False)
    _taken: set[str] = field(default_factory=set, init=False, repr=False)

    def new_scope(self, taken: Iterable[str] = ()) -> None:
        """Start a new output scope (scenario); `taken` are names already in use (personas)."""
        self._assigned = {}
        self._taken = {t.lower() for t in taken}

    def identity(self, ref: str) -> Identity:
        if ref in self._assigned:
            return self._assigned[ref]
        kind, _, num_s = ref.partition(":")
        num = int(num_s)
        if kind == "o":
            return self._org_identity(ref, num)
        if kind == "l":
            return self._place_identity(ref, num)
        meta = (self.people if kind == "p" else self.externals if kind == "x" else {}).get(num, {})
        org = meta.get("org")
        ident = fake_identity(self.seed, kind, num, real=self.real,
                              reserved=self.reserved | frozenset(self._taken),
                              internal=bool(meta.get("internal", kind in ("q", "s"))),
                              org=org if isinstance(org, int) else None)  # fmt: skip
        self._assigned[ref] = ident
        self._taken.add(ident.name.lower())
        return ident

    def _org_identity(self, ref: str, num: int) -> Identity:
        """An organisation pseudonym from the org pool (never a person-like name)."""
        pool = N.FAKE_ORG_NAMES
        used = {i.first for r, i in self._assigned.items() if r.startswith("o:")}
        start = stable_hash(self.seed, "o", num) % len(pool)
        name = next((pool[(start + k) % len(pool)] for k in range(len(pool))
                     if pool[(start + k) % len(pool)] not in used), pool[start])  # fmt: skip
        ident = Identity(name, "", f"info@{name.lower()}.example.test")
        self._assigned[ref] = ident
        return ident

    def _place_identity(self, ref: str, num: int) -> Identity:
        """A place / asset pseudonym from the place pool (never a person or firm name)."""
        pool = N.FAKE_PLACES
        used = {i.first for r, i in self._assigned.items() if r.startswith("l:")}
        start = stable_hash(self.seed, "l", num) % len(pool)
        name = next((pool[(start + k) % len(pool)] for k in range(len(pool))
                     if pool[(start + k) % len(pool)] not in used), pool[start])  # fmt: skip
        ident = Identity(name, "", f"info@{name.lower()}.example.test")
        self._assigned[ref] = ident
        return ident

    def render(self, template: str, binding: dict[str, Identity] | None = None) -> str:
        binding = binding or {}

        def sub(m: re.Match[str]) -> str:
            ref = f"{m.group(1)}:{m.group(2)}"
            ident = binding.get(ref) or self.identity(ref)
            form = m.group(3)
            if form == "f":
                return ident.first
            if form == "l":
                return ident.last
            if form == "e":
                return ident.email
            return ident.name

        return PLACEHOLDER.sub(sub, template)


def org_index(seed: int, domain: str | None) -> int | None:
    if not domain:
        return None
    return stable_hash(seed, "org", domain) % len(N.FAKE_ORGS)


def people_meta(registry: Registry, seed: int, pids: Iterable[int]) -> dict[int, dict[str, object]]:
    out: dict[int, dict[str, object]] = {}
    for pid in sorted(set(pids)):
        person = registry.people[pid]
        out[pid] = {"internal": person.internal, "org": org_index(seed, person.domain)}
    return out


def external_meta(pseudo: Pseudonymizer, xids: Iterable[int]) -> dict[int, dict[str, object]]:
    out: dict[int, dict[str, object]] = {}
    for xid in sorted(set(xids)):
        domain = pseudo.email_domains[xid]
        internal = "enron" in domain
        out[xid] = {
            "internal": internal,
            "org": None if internal else org_index(pseudo.seed, domain),
        }
    return out
