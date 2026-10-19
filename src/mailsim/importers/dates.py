"""Date normalisation for corpus-derived mail.

Bodies were written in 1999-2002. Every date in one record (all segments of a thread) moves
by the same whole number of weeks, chosen so the record's reference date (the latest date of
its main year) lands just before the thread's newest message and never after the scenario
start. Whole weeks keep weekdays right ("Wednesday, July 5" stays a Wednesday), one shift per
record keeps a thread internally consistent, and bare years ("in 2001", "Q3 2001", "summer
02") move by the matching number of years.

Recognised forms: ``Wednesday, July 5``, ``November 12 & 13, 2001``, ``Oct. 16th``,
``08 December 2001``, ``March of 2001``, ``4/23/01``, ``10/10/2001``, ``2001-10-10``,
``on 12/15`` (month/day after a date preposition or at a line start), years in date contexts
and two-digit season/period years (``summer 02``). A year-less date is placed in the year that
puts it nearest the reference date (or, with a weekday, the year in which that weekday fits).
A record without any full date only has its years moved. A 1995-2003 number counts as a year
unless it reads as a quantity or an identifier ("2000 MW", "deal 2001", "$1,999").
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

    for rx, fn in ((TEXT_DATE, text_date), (DAY_MONTH, text_date), (NUMERIC, numeric),
                   (ISO, iso), (MONTH_YEAR, month_year), (MONTH_DAY, month_day)):  # fmt: skip
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
