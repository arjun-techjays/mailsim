"""Date normalisation for corpus-derived mail.

Bodies were written in 1999-2002. Every date in one record (all segments of a thread) moves
by the same whole number of weeks, chosen so the record's reference date (the latest date of
its main year) lands just before the thread's newest message and never after the scenario
start. Whole weeks keep weekdays right ("Wednesday, July 5" stays a Wednesday), one shift per
record keeps a thread internally consistent, and bare years ("in 2001", "Q3 2001", "summer
02") move by the matching number of years.

Recognised forms: ``Wednesday, July 5``, ``November 12 & 13, 2001``, ``Oct. 16th``,
``08 December 2001``, ``March of 2001``, ``4/23/01``, ``10/10/2001``, ``2001-10-10``,
``on 12/15`` (month/day after a date preposition or at a line start), month-only mentions
(``early July``, ``by June``), years in date contexts and two-digit season/period years
(``summer 02``). A year-less date is placed in the year that puts it nearest the reference
date (or, with a weekday, the year in which that weekday fits). A 1995-2003 number counts as a
year unless it reads as a quantity or an identifier ("2000 MW", "deal 2001", "$1,999").

`plan_record` adds the checks against the message dates: a date in future tense ("will",
"please plan to attend", "scheduled", "effective") must not land before the message that
mentions it, a date in past tense not after it. A record whose only dates are year-less is
anchored to its newest message (just after it for future tense, just before for past tense);
without a tense marker, or when no shift satisfies the checks, the record is not usable (None).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

FULL_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")  # fmt: skip
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NO = {m.lower(): i for i, m in enumerate(FULL_MONTHS, start=1)}
_MONTH_NO.update({m[:3].lower(): i for i, m in enumerate(FULL_MONTHS, start=1)})
_MONTH_NO["sept"] = 9
_WD_NO = {w.lower(): i for i, w in enumerate(WEEKDAYS)}
_WD_NO.update({w[:3].lower(): i for i, w in enumerate(WEEKDAYS)})
_WD_NO.update({"tues": 1, "wed": 2, "thur": 3, "thurs": 3})

_MON = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
_WD = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tues|Tue|Wed|Thurs|Thur|Thu|Fri|Sat|Sun)"
_ORD = r"(?:st|nd|rd|th)?"
TEXT_DATE = re.compile(
    rf"(?:(?P<wd>{_WD})(?P<wddot>\.?),?\s+)?(?P<mon>{_MON})(?P<mdot>\.?)\s+(?P<d1>3[01]|[12]\d|0?[1-9])(?P<o1>{_ORD})"
    rf"(?:(?P<sep>\s*(?:&|and|-|–|to|through|thru)\s*)(?P<d2>\d{{1,2}})(?P<o2>{_ORD}))?"
    rf"(?:(?P<comma>\s*,\s*|\s+)(?P<y>(?:19|20)\d\d))?(?![\d:,.]\d)(?!\d)"
)
DAY_MONTH = re.compile(
    rf"\b(?P<d1>\d{{1,2}})(?P<o1>{_ORD})\s+(?P<mon>{_MON})(?P<mdot>\.?)(?P<comma>,?\s+)(?P<y>(?:19|20)\d\d)\b"
)
MONTH_YEAR = re.compile(
    rf"\b(?P<mon>{_MON})(?P<mdot>\.?)(?P<of>\s+(?:of\s+)?)(?P<y>(?:19|20)\d\d)\b"
)
NUMERIC = re.compile(
    r"(?<![\d/.\-$])(?P<m>\d{1,2})(?P<s>[/-])(?P<d>\d{1,2})(?P=s)(?P<y>(?:19|20)\d\d|\d\d)(?![\d/]|-\d)"
)
ISO = re.compile(r"(?<![\d\-])(?P<y>(?:19|20)\d\d)-(?P<m>\d\d)-(?P<d>\d\d)(?![\d\-])")
MONTH_DAY = re.compile(
    r"(?P<pre>(?:\b(?:on|by|for|thru|through|until|till|effective|dated|after|before|from|"
    r"starting|beginning|of|as of)\s+)|(?m:^[ \t]*))(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/%]|\.\d)",
    re.IGNORECASE,
)
SHORT_YEAR = re.compile(
    rf"(?P<pre>\b(?:[Ss]ummer|[Ww]inter|[Ss]pring|[Ff]all|[Aa]utumn|[Cc]al|CAL|Q[1-4]|[1-4]Q|FY|[Yy]ear|"
    rf"{_MON}\.?)\s?'?)"
    r"(?P<yy>9[5-9]|0[0-3])\b(?![/\-.]\d)"
)
YEAR = re.compile(r"\b(199[5-9]|200[0-3])\b")
# a month named alone after a time word: "early July", "by June", "end of March"
MONTH_ONLY = re.compile(
    r"(?P<pre>\b(?:[Ee]arly|[Mm]id|[Ll]ate|[Ee]nd of|[Bb]eginning of|[Ss]tart of|[Mm]iddle of|"
    r"[Ii]n|[Bb]y|[Uu]ntil|[Tt]ill|[Tt]hrough|[Ss]ince|[Dd]uring|[Ff]or|[Nn]ext|[Ll]ast|[Tt]his|"
    r"[Bb]efore|[Aa]fter|[Ff]rom)(?:[ \t-]+|[ \t]*\n[ \t]*))(?P<mon>January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan\.|Feb\.|Aug\.|Sept?\.|Oct\.|Nov\.|Dec\.)"
    r"(?![a-z])(?![ \t]*(?:\d|of\b|,\s*\d))"
)
_MONTH_ONLY_DAY = {"early": 5, "beginning of": 3, "start of": 3, "mid": 15, "middle of": 15,
                   "late": 25, "end of": 28}  # fmt: skip
FUTURE = re.compile(
    r"\b(?:will|shall|plan(?:s|ning)? to|please (?:plan|attend|join|come|mark|hold|save)|"
    r"scheduled|is set for|are set for|upcoming|going to|effective|due|deadline|no later than|"
    r"starting|beginning|begins|commenc\w+|rsvp|register by|look(?:ing)? forward|"
    r"(?:this|next) (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week))\b",
    re.IGNORECASE,
)
PAST = re.compile(
    r"\b(?:was|were|had|has been|have been|did|last|ago|yesterday|happened|occurred|took place|"
    r"ended|signed|sent|received|met|held|closed|finished|formed|created|established|founded|"
    r"launched|started|began|opened|announced|approved|completed|moved|joined|hired|used|"
    r"prepared|provided|reported|submitted|issued|posted|published|filed|circulated)\b",
    re.IGNORECASE,
)
# month names and month/day dates that no date pattern covers ("in October, July and August",
# "for Tuesday, 6/19", "This was 12/3"): a record with one cannot be shifted consistently
_BARE_MONTH = re.compile(
    r"\b(?:January|February|March|April|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\b"
)
_WEEKDAY_ORDINAL = re.compile(rf"\b{_WD},?\s+the\s+\d{{1,2}}(?:st|nd|rd|th)\b")
_MONTH_YY = re.compile(r"(?<![\d/])(?:0[1-9]|1[0-2])/(?:9[5-9]|0[0-3])(?![\d/])")
_DASH_RANGE = re.compile(r"\b\d{1,2}-\d{2}\s+(?:thru|through|to|-)\s+\d{1,2}-\d{2}\b")
_BARE_MD = re.compile(r"(?<![\d/.\-$:])(\d{1,2})/(\d{1,2})(?![\d/%:]|\.\d)")
_PASSIVE = re.compile(r"\b(?:will|shall|to|would|be|being)\s+(?:be\s+)?$", re.IGNORECASE)
_WEEKDAY_LEAD = re.compile(rf"{_WD}\b")
_UPCOMING = re.compile(r"\b(?:this|next|on|coming|until|by)\s+$", re.IGNORECASE)
NOMINAL_YEAR = 2001  # the corpus year a year-less date is read in when nothing else says
# a 1995-2003 number is a year unless it is a quantity ("2000 MW", "$2001", "2,001") or an id
_NOT_YEAR_BEFORE = re.compile(
    r"(?:[$#.,]|(?<![A-Za-z])\d[ ,]|\b(?:meter|deal|contract|room|suite|no\.?|number|ext|x|rule|order|item|"
    r"ticket|account|po|invoice|ref|route|highway|hwy|line|unit|model|version|v|windows|office|"
    r"exchange|server|win)\s*#?\s*)$",
    re.IGNORECASE,
)
_NOT_YEAR_AFTER = re.compile(
    r"(?:[.,]\d|\s*(?:mw|mwh|gw|gwh|kw|kwh|kv|mmbtu|btu|dth|bcf|mcf|bbl|barrels|tons|shares|units|"
    r"lbs|feet|ft|miles|people|employees|customers|%|x\b|hours|hrs|minutes|cars|homes)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Shift:
    days: int = 0  # whole weeks, in days
    years: int = 0  # for bare years
    reference: int | None = None  # the year a year-less date belongs to (None: leave it)
    anchor: date | None = None


def _safe(y: int, m: int, d: int) -> date | None:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    for day in (d, 30, 29, 28):
        if day <= d:
            try:
                return date(y, m, day)
            except ValueError:
                continue
    return None  # pragma: no cover - day 28 always exists


def _year(text: str) -> int:
    y = int(text)
    if len(text) == 2:
        return 2000 + y if y < 50 else 1900 + y
    return y


def _infer_year(m: int, d: int, weekday: int | None) -> int | None:
    """The corpus year (latest first) in which a weekday + month/day fits."""
    if weekday is None:
        return None
    for y in range(2002, 1996, -1):
        when = _safe(y, m, d)
        if when is not None and when.weekday() == weekday and when.day == d:
            return y
    return None


def _full_dates(text: str) -> tuple[list[date], list[int]]:
    """Dates with a known year (explicit or weekday-inferred) and bare years in `text`."""
    dates: list[date] = []
    for rx in (TEXT_DATE, DAY_MONTH):
        for m in rx.finditer(text):
            mon = _MONTH_NO[m.group("mon").lower()]
            d1 = int(m.group("d1"))
            y = m.group("y")
            wd = m.groupdict().get("wd")
            year = _year(y) if y else _infer_year(mon, d1, _WD_NO[wd.lower()] if wd else None)
            when = _safe(year, mon, d1) if year else None
            if when:
                dates.append(when)
    for m in NUMERIC.finditer(text):
        when = _safe(_year(m.group("y")), int(m.group("m")), int(m.group("d")))
        if when:
            dates.append(when)
    for m in ISO.finditer(text):
        when = _safe(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        if when:
            dates.append(when)
    for m in MONTH_YEAR.finditer(text):
        dates.append(date(int(m.group("y")), _MONTH_NO[m.group("mon").lower()], 15))
    years = [int(y) for y in YEAR.findall(text)]
    years += [_year(m.group("yy")) for m in SHORT_YEAR.finditer(text)]
    return dates, years


def _month_only_day(pre: str) -> int:
    return _MONTH_ONLY_DAY.get(pre.strip(" \t-").lower(), 15)


def _yearless(text: str) -> list[tuple[int, int, int, int]]:
    """Year-less dates without a weekday to place them: (month, day, start, end)."""
    out: list[tuple[int, int, int, int]] = []
    for rx in (TEXT_DATE,):
        for m in rx.finditer(text):
            if m.group("y") or (m.group("wd") and _infer_year(
                    _MONTH_NO[m.group("mon").lower()], int(m.group("d1")),
                    _WD_NO[m.group("wd").lower()]) is not None):  # fmt: skip
                continue
            out.append((_MONTH_NO[m.group("mon").lower()], int(m.group("d1")), m.start(), m.end()))
    for m in MONTH_DAY.finditer(text):
        if 1 <= int(m.group("m")) <= 12 and 1 <= int(m.group("d")) <= 31:
            out.append((int(m.group("m")), int(m.group("d")), m.start("m"), m.end()))
    for m in MONTH_ONLY.finditer(text):
        mon = _MONTH_NO[m.group("mon").rstrip(".").lower()]
        out.append((mon, _month_only_day(m.group("pre")), m.start("mon"), m.end()))
    return sorted(out, key=lambda x: x[2])


def unplaced(text: str) -> list[str]:
    """Month names and month/day dates outside every recognised date form (see `plan_record`).
    Small fractions ("1/2", "3/4") are not dates."""
    covered = [(m.start(), m.end()) for rx in (TEXT_DATE, DAY_MONTH, MONTH_YEAR, NUMERIC, ISO,
                                              MONTH_DAY, MONTH_ONLY)
               for m in rx.finditer(text)]  # fmt: skip

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in covered)

    out = [m.group(0) for m in _BARE_MONTH.finditer(text) if free(m.start(), m.end())]
    out += [m.group(0) for m in _WEEKDAY_ORDINAL.finditer(text)]  # "Monday the 30th"
    out += [m.group(0) for m in _DASH_RANGE.finditer(text) if free(m.start(), m.end())]
    out += [m.group(0) for m in _MONTH_YY.finditer(text) if free(m.start(), m.end())]
    for m in _BARE_MD.finditer(text):
        mon, day = int(m.group(1)), int(m.group(2))
        fraction = mon < day <= 4 or (mon, day) in ((1, 1),)
        if 1 <= mon <= 12 and 1 <= day <= 31 and not fraction and free(m.start(), m.end()):
            out.append(m.group(0))
    return out


def tense_at(text: str, start: int, end: int) -> str | None:
    """ "future" / "past" from the nearest tense marker before a date (same passage, up to 200
    characters back) or right after it in the same sentence; None without a marker."""
    before = text[max(0, start - 200) : start]
    if _WEEKDAY_LEAD.match(text, start) and _UPCOMING.search(before):
        # "this Friday, October 2", "on Monday, May 4": upcoming unless a past verb is right there
        near = re.split(r"[.!?\n]", before[-40:])[-1]  # the same sentence only
        past = [m for m in PAST.finditer(near)
                if not _PASSIVE.search(near[max(0, m.start() - 16) : m.start()])]  # fmt: skip
        return "past" if past else "future"
    sentence_end = re.search(r"[.!?\n]", text[end:])
    after = text[end : end + (sentence_end.start() if sentence_end else 60)][:60]
    fut = [m.end() for m in FUTURE.finditer(before)]
    pst = [m.end() for m in PAST.finditer(before)  # "will be signed" is not past
           if not _PASSIVE.search(before[max(0, m.start() - 16) : m.start()])]  # fmt: skip
    if fut or pst:
        return "future" if max(fut, default=-1) > max(pst, default=-1) else "past"
    if FUTURE.search(after):
        return "future"
    if PAST.search(after):
        return "past"
    return None


def _yearless_shift(text: str, found: list[tuple[int, int, int, int]], years: list[int],
                    target: date) -> Shift:  # fmt: skip
    """Day shift that puts the latest year-less date of a record on `target`."""
    reference = max(years) if years else NOMINAL_YEAR
    first = _safe(reference, found[0][0], found[0][1])
    assert first is not None  # validated months and days
    placed = []
    for mon, day, _s, _e in found:
        options = [x for x in (_safe(reference + k, mon, day) for k in (-1, 0, 1)) if x]
        placed.append(min(options, key=lambda x: abs((x - first).days)))
    anchor = max(placed)
    days = (target - anchor).days
    moved = anchor + timedelta(days=days)
    return Shift(days, moved.year - anchor.year, anchor.year, anchor)


def plan_record(items: list[tuple[str, date]], last: date, limit: date) -> Shift | None:
    """The shift for one record from (text, date of the message it appears in) pairs; None if
    no shift keeps every dated mention on the right side of its message (see module doc)."""
    texts = [t for t, _ in items]
    blob = "\n".join(texts)
    if unplaced(blob):
        return None
    dates, years = _full_dates(blob)
    if dates:
        for target in (last + timedelta(days=3), last + timedelta(days=7)):
            shift = plan(texts, min(target, limit), limit)
            if not violations(items, shift):
                return shift
        return None
    found = _yearless(blob)
    if found:
        tenses = {tense_at(blob, s, e) for _m, _d, s, e in found}
        if None in tenses or len(tenses) > 1:
            return None  # a year-less date with no (or mixed) tense cannot be placed
        target = last + timedelta(days=3) if tenses == {"future"} else last - timedelta(days=2)
        shift = _yearless_shift(blob, found, years, min(target, limit))
        return None if violations(items, shift) else shift
    return plan(texts, last + timedelta(days=3), limit)


def mentions(text: str, around: date) -> list[tuple[date, date, int, int]]:
    """Dates mentioned in already shifted text as (earliest, latest, start, end): a day, or a
    whole month for a month-only mention. A year-less one is read in the year nearest
    `around` (with a weekday: the nearby year in which it fits)."""
    out: list[tuple[date, date, int, int]] = []

    def near(mon: int, day: int, weekday: int | None) -> date | None:
        options = [x for x in (_safe(around.year + k, mon, day) for k in (-1, 0, 1)) if x]
        if weekday is not None:
            fitting = [x for x in options if x.weekday() == weekday and x.day == day]
            options = fitting or options
        return min(options, key=lambda x: abs((x - around).days)) if options else None

    taken: list[tuple[int, int]] = []
    for rx in (TEXT_DATE, DAY_MONTH):
        for m in rx.finditer(text):
            mon, d1 = _MONTH_NO[m.group("mon").lower()], int(m.group("d1"))
            wd = m.groupdict().get("wd")
            y = m.group("y")
            weekday = _WD_NO[wd.lower()] if wd else None
            when = _safe(_year(y), mon, d1) if y else near(mon, d1, weekday)
            if when:
                out.append((when, when, m.start(), m.end()))
                taken.append((m.start(), m.end()))
    for rx in (NUMERIC, ISO):
        for m in rx.finditer(text):
            when = _safe(_year(m.group("y")), int(m.group("m")), int(m.group("d")))
            if when:
                out.append((when, when, m.start(), m.end()))
                taken.append((m.start(), m.end()))

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for m in MONTH_DAY.finditer(text):
        when = near(int(m.group("m")), int(m.group("d")), None)
        if when and free(m.start("m"), m.end()):
            out.append((when, when, m.start("m"), m.end()))
    for m in MONTH_ONLY.finditer(text):
        mon = _MONTH_NO[m.group("mon").rstrip(".").lower()]
        mid = near(mon, 15, None)
        if mid and free(m.start("mon"), m.end()):
            first = mid.replace(day=1)
            last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            out.append((first, last, m.start("mon"), m.end()))
    return sorted(out, key=lambda x: x[2])


def date_conflicts(text: str, sent: date) -> list[str]:
    """Dates of `text` (already shifted) on the wrong side of its send date: future tense
    before it, past tense after it."""
    out = []
    for first, last, start, end in mentions(text, sent):
        tense = tense_at(text, start, end)
        if (tense == "future" and last < sent) or (tense == "past" and first > sent):
            out.append(f"{text[start:end]} ({tense}, {first.isoformat()} vs {sent.isoformat()})")
    return out


def violations(items: list[tuple[str, date]], shift: Shift) -> list[str]:
    return [c for text, sent in items for c in date_conflicts(apply(text, shift), sent)]


def plan(texts: list[str], target: date, limit: date) -> Shift:
    """The shift for one record: its reference date lands in (T - 7 days, T], where T is
    min(target, limit); `limit` is the last allowed date (the day before the clock start)."""
    blob = "\n".join(texts)
    dates, years = _full_dates(blob)
    end = min(target, limit)
    if dates:
        counts = Counter(d.year for d in dates)
        main = max(counts, key=lambda y: (counts[y], y))
        latest_main = max(d for d in dates if d.year == main)
        # the latest date, unless it is far beyond the main year (a contract term, an expiry)
        anchor = max(d for d in dates if d <= latest_main + timedelta(days=400))
        days = 7 * ((end - anchor).days // 7)
        moved = anchor + timedelta(days=days)
        return Shift(days, moved.year - anchor.year, main, anchor)
    if years:
        return Shift(0, end.year - max(years), None, None)
    return Shift()


def _fmt_month(original: str, month: int, dot: str) -> str:
    full = FULL_MONTHS[month - 1]
    if original in FULL_MONTHS:
        return full
    short = "Sept" if original == "Sept" and month == 9 else full[:3]
    return short + dot


def _fmt_weekday(original: str, weekday: int, dot: str) -> str:
    full = WEEKDAYS[weekday]
    return full if original in WEEKDAYS else full[:3] + dot


def _suffix(day: int, original: str) -> str:
    if not original:
        return ""
    return "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _ordinal(day: int, original: str) -> str:
    return f"{day}{_suffix(day, original)}"


def _place(mon: int, day: int, shift: Shift, weekday: int | None) -> date | None:
    """A year-less month/day as a full date near the reference (None: cannot place it)."""
    inferred = _infer_year(mon, day, weekday)
    if inferred is not None:
        return _safe(inferred, mon, day)
    if shift.reference is None or shift.anchor is None:
        return None
    anchor = shift.anchor
    options = [x for x in (_safe(shift.reference + k, mon, day) for k in (-1, 0, 1)) if x]
    return min(options, key=lambda x: abs((x - anchor).days)) if options else None


def apply(text: str, shift: Shift) -> str:
    """Rewrite every recognised date in `text` with `shift` (see `plan`)."""
    if shift == Shift():
        return text
    delta = timedelta(days=shift.days)
    spans: list[tuple[int, int, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _ in spans)

    def text_date(m: re.Match[str]) -> str | None:
        mon = _MONTH_NO[m.group("mon").lower()]
        d1 = int(m.group("d1"))
        wd = m.groupdict().get("wd")
        y = m.group("y")
        if y:
            base = _safe(_year(y), mon, d1)
        else:
            base = _place(mon, d1, shift, _WD_NO[wd.lower()] if wd else None)
        if base is None or not shift.days and not y:
            return None
        new = base + delta
        out = ""
        if wd:
            out = _fmt_weekday(wd, new.weekday(), m.group("wddot")) + ", "
        month = _fmt_month(m.group("mon"), new.month, m.group("mdot"))
        if m.re is DAY_MONTH:
            day = f"{new.day:0{len(m.group('d1'))}d}{_suffix(new.day, m.group('o1'))}"
            out += f"{day} {month}"
        else:
            out += f"{month} {_ordinal(new.day, m.group('o1'))}"
        d2 = m.groupdict().get("d2")
        if d2:
            end = _safe(base.year, base.month, int(d2))
            if end is not None and end >= base:
                end = end + delta
                tail = _ordinal(end.day, m.group("o2"))
                if end.month != new.month:
                    tail = f"{_fmt_month(m.group('mon'), end.month, m.group('mdot'))} {tail}"
                out += f"{m.group('sep')}{tail}"
            else:
                out += f"{m.group('sep')}{d2}{m.group('o2')}"
        if y:
            out += f"{m.group('comma')}{new.year}"
        return out

    def numeric(m: re.Match[str]) -> str | None:
        base = _safe(_year(m.group("y")), int(m.group("m")), int(m.group("d")))
        if base is None:
            return None
        new = base + delta
        sep = m.group("s")
        mm = (
            f"{new.month:0{len(m.group('m'))}d}" if m.group("m").startswith("0") else str(new.month)
        )
        dd = f"{new.day:02d}" if m.group("d").startswith("0") else str(new.day)
        yy = f"{new.year % 100:02d}" if len(m.group("y")) == 2 else str(new.year)
        return f"{mm}{sep}{dd}{sep}{yy}"

    def iso(m: re.Match[str]) -> str | None:
        base = _safe(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        return (base + delta).isoformat() if base else None

    def month_day(m: re.Match[str]) -> str | None:
        mon, day = int(m.group("m")), int(m.group("d"))
        base = _place(mon, day, shift, None)
        if base is None or not shift.days:
            return None
        new = base + delta
        mm = f"{new.month:02d}" if m.group("m").startswith("0") else str(new.month)
        dd = f"{new.day:02d}" if m.group("d").startswith("0") else str(new.day)
        return f"{m.group('pre')}{mm}/{dd}"

    def month_year(m: re.Match[str]) -> str | None:
        if not shift.days:
            return f"{m.group('mon')}{m.group('mdot')}{m.group('of')}{_bare(m.group('y'))}"
        new = date(int(m.group("y")), _MONTH_NO[m.group("mon").lower()], 15) + delta
        return f"{_fmt_month(m.group('mon'), new.month, m.group('mdot'))}{m.group('of')}{new.year}"

    def _bare(y: str) -> str:
        return str(int(y) + shift.years)

    def month_only(m: re.Match[str]) -> str | None:
        if not shift.days:
            return None
        mon = _MONTH_NO[m.group("mon").rstrip(".").lower()]
        base = _place(mon, _month_only_day(m.group("pre")), shift, None)
        if base is None:
            return None
        new = base + delta
        dot = "." if m.group("mon").endswith(".") else ""
        return f"{m.group('pre')}{_fmt_month(m.group('mon').rstrip('.'), new.month, dot)}"

    for rx, fn in ((TEXT_DATE, text_date), (DAY_MONTH, text_date), (NUMERIC, numeric),
                   (ISO, iso), (MONTH_YEAR, month_year), (MONTH_DAY, month_day),
                   (MONTH_ONLY, month_only)):  # fmt: skip
        for m in rx.finditer(text):
            if not free(m.start(), m.end()):
                continue
            new = fn(m)
            if new is not None and new != m.group(0):
                spans.append((m.start(), m.end(), new))
            elif new is not None or m.re in (TEXT_DATE, DAY_MONTH, NUMERIC, ISO):
                spans.append((m.start(), m.end(), m.group(0)))  # a date: keep its years as-is
    if shift.years:
        for m in SHORT_YEAR.finditer(text):
            if free(m.start(), m.end()):
                yy = (_year(m.group("yy")) + shift.years) % 100
                spans.append((m.start(), m.end(), f"{m.group('pre')}{yy:02d}"))
        for m in YEAR.finditer(text):
            if not free(m.start(), m.end()):
                continue
            before = text[max(0, m.start() - 12) : m.start()]
            after = text[m.end() : m.end() + 12]
            if not (_NOT_YEAR_BEFORE.search(before) or _NOT_YEAR_AFTER.match(after)):
                spans.append((m.start(), m.end(), _bare(m.group(1))))
    out = []
    pos = 0
    for start, end, new in sorted(spans):
        out.append(text[pos:start])
        out.append(new)
        pos = end
    out.append(text[pos:])
    return "".join(out)
