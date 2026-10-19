"""Attachment documents for generated leadership mail.

A message whose body says something is attached ("Attached is ...", "Enclosed ...",
"<<file.doc>>") gets a real document: a multi-page PDF (or an XLSX for spreadsheets) whose
title, dates, parties and figures follow the email. Kinds: board pre-read deck, credit watch
list, invoice, draft agreement, schedule, report / financial summary, spreadsheet and memo.
Every figure and name is fictional and seeded (`stable_hash`), so the same inputs give the
same bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from mailsim.importers import names as N
from mailsim.importers.attachments import Line, pdf_bytes, xlsx_bytes
from mailsim.importers.pseudonymize import stable_hash

# The body says a file comes with it (an attachment stub line or an explicit claim).
ATTACH_CLAIM = re.compile(
    r"\b(?:(?:attached|enclosed) (?:is|are|please|you|for|herewith|hereto|below|here|to|file|"
    r"document|spreadsheet|worksheet|draft|report|memo|list|copy|version|letter|presentation|"
    r"schedule|form|agreement|summary|outline|notes)|(?:the|my|our|see|find|review|per the|in "
    r"the|on the|with the) (?:attached|enclosed)|attachments?|i(?:'ve| have) attached|"
    r"i am attaching|i'm attaching|attaching|(?:is|are) attached|i attach|it'?s attached)\b"
    r"|<<[^<>\n]+>>|\bhere (?:is|are) (?:the|a|my|our) (?:latest |revised |updated |final |new |"
    r"draft |current )*(?:version|draft|copy|list|file|spreadsheet|report|schedule|memo|letter|"
    r"presentation|document|summary|agenda|slides|numbers|figures)\b",
    re.IGNORECASE,
)
# Lotus/Outlook attachment stubs left in bodies (" - report.doc", "<<x5503.DOC>>")
_EXT = r"(?:docx?|xlsx?|xls|pdf|pptx?|txt|zip|csv|wpd|rtf|url)"
STUB_LINE = re.compile(
    rf"(?im)^[ \t]*(?:-[ \t]+)?(?:<<[^<>]{{1,120}}>>|[\w.()\-][\w.()\- ]{{0,80}}\.{_EXT}|"
    rf"\(See attached file: [^()\n]{{1,120}}\))[ \t]*$\n?"
)
INLINE_STUB = re.compile(r"[ \t]*<<[^<>]{1,120}>>")


def stub_title(stub: str) -> str | None:
    """A readable title from an attachment stub ("<<Comparison of Turbine Agreement.DOC>>")."""
    name = re.sub(
        r"^[\s<-]+|[\s>]+$", "", re.sub(r"(?i)^\s*\(see attached file:\s*|\)\s*$", "", stub)
    )
    stem = re.sub(rf"\.{_EXT}$", "", re.sub(r"\s+", " ", name), flags=re.I)
    words = re.findall(r"[A-Za-z][A-Za-z&']+", stem)
    if len([w for w in words if len(w) >= 4]) < 2 or re.search(r"\d{3,}", stem):
        return None
    return _title(" ".join(words))


KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credit_watch", re.compile(r"credit watch", re.I)),
    ("roster", re.compile(r"\b(?:committee|contact|distribution|phone|member|attendee|team) list\b",
                          re.I)),
    ("invoice", re.compile(r"\binvoices?\b|\bbilling\b", re.I)),
    ("spreadsheet", re.compile(r"\b(?:spreadsheet|excel|xls|worksheet|workbook)\b", re.I)),
    ("contract", re.compile(r"\b(?:agreement|contract|confirm(?:ation)?s?|term sheet|amendment|"
                            r"lc form|letter of credit|guarant\w+|tariff|rate schedule)\b", re.I)),
    ("schedule", re.compile(r"\b(?:schedule|calendar|itinerary|agenda|timeline)\b", re.I)),
    ("report", re.compile(r"\b(?:report|summary|analysis|forecast|budget|p&l|statement|results|"
                          r"presentation|outline|review|position|listing|survey|study)\b", re.I)),
)  # fmt: skip


@dataclass(frozen=True)
class Doc:
    filename: str
    content_type: str
    data: bytes


def kind_of(text: str) -> str:
    for kind, rx in KINDS:
        if rx.search(text):
            return kind
    return "memo"


def _title(subject: str) -> str:
    s = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", subject, flags=re.I).strip(" .:-")
    s = re.sub(r"[^\w&' -]+", " ", s)
    words = s.split()[:7] or ["Document"]
    small = {"of", "the", "and", "for", "to", "in", "on", "a", "an", "with", "at", "by"}
    return " ".join(
        w if w.isupper() else w.lower() if i and w.lower() in small else w[:1].upper() + w[1:]
        for i, w in enumerate(words)
    )


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:48] or "document"


def _org(seed: int, key: str, i: int) -> str:
    pool = N.FAKE_ORG_NAMES
    suffix = ("Energy LLC", "Power Inc.", "Gas Marketing LP", "Resources Corp.", "Trading Ltd.",
              "Utilities Inc.", "Capital LLC", "Pipeline Co.")  # fmt: skip
    h = stable_hash(seed, "doc-org", key, i)
    return f"{pool[h % len(pool)]} {suffix[(h >> 8) % len(suffix)]}"


def _money(seed: int, key: str, i: int, lo: int, hi: int) -> int:
    return lo + stable_hash(seed, "doc-num", key, i) % (hi - lo + 1)


def _wrap_excerpt(body: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", body).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")] + " ..."


@dataclass(frozen=True)
class Context:
    seed: int
    key: str  # stable per message (fixture identity)
    subject: str
    body: str
    sender_name: str
    sender_role: str
    recipient_name: str
    when: datetime


def _header(ctx: Context, title: str, label: str) -> list[Line]:
    return [
        Line("small", f"{N.COMPANY_LONG}  |  {label}"),
        Line("title", title),
        Line("p", f"Prepared by {ctx.sender_name}, {ctx.sender_role}"),
        Line("p", f"Date: {ctx.when.strftime('%B %d, %Y').replace(' 0', ' ')}"),
        Line("p", ""),
    ]


def _credit_watch(ctx: Context, title: str) -> list[list[Line]]:
    statuses = ("No Trades", "Watch", "Restricted - prepay", "Collateral call", "Reduced tenor")
    rows = [Line("mono", f"{'Counterparty':<34}{'Status':<22}{'Limit (USD k)':>14}")]
    rows.append(Line("mono", "-" * 70))
    for i in range(14):
        name = _org(ctx.seed, ctx.key, i)
        status = statuses[stable_hash(ctx.seed, ctx.key, "st", i) % len(statuses)]
        limit = _money(ctx.seed, ctx.key, i, 0, 40) * 250
        rows.append(Line("mono", f"{name[:33]:<34}{status:<22}{limit:>14,}"))
    page1 = _header(ctx, title, "Credit Risk Management") + [
        Line("h2", "Purpose"),
        Line("p", "This listing shows counterparties whose trading status changed this week. "
                  "Desks must check the status before entering any new transaction."),
        Line("h2", "Counterparties on watch"),
        *rows,
    ]  # fmt: skip
    page2 = [
        Line("h1", "Status definitions"),
        Line("p", "No Trades: no new transactions of any tenor until Credit clears the name."),
        Line("p", "Watch: trade within existing limits; notify Credit before any term deal."),
        Line("p", "Restricted - prepay: physical deliveries only against prepayment."),
        Line("p", "Collateral call: margin requested; new trades need Credit approval."),
        Line("p", "Reduced tenor: maximum tenor reduced to one month."),
        Line("h2", "Distribution note"),
        Line("p", _wrap_excerpt(ctx.body)),
        Line("p", f"Questions: {ctx.sender_name}."),
    ]
    return [page1, page2]


def _invoice(ctx: Context, title: str) -> list[list[Line]]:
    vendor = _org(ctx.seed, ctx.key, 99)
    number = f"INV-{ctx.when.year}-{_money(ctx.seed, ctx.key, 0, 1000, 9999)}"
    items = ("Transportation charges", "Storage fees", "Balancing charges", "Service fee",
             "Late payment interest", "Administrative fee")  # fmt: skip
    lines = [Line("mono", f"{'Description':<40}{'Qty':>8}{'Amount (USD)':>18}"),
             Line("mono", "-" * 66)]  # fmt: skip
    total = 0
    for i, item in enumerate(items[: 3 + stable_hash(ctx.seed, ctx.key, "n") % 3]):
        qty = _money(ctx.seed, ctx.key, 10 + i, 1, 30)
        amount = _money(ctx.seed, ctx.key, 20 + i, 400, 9000) * qty
        total += amount
        lines.append(Line("mono", f"{item:<40}{qty:>8}{amount:>18,.2f}"))
    lines += [Line("mono", "-" * 66), Line("mono", f"{'Total due':<48}{total:>18,.2f}")]
    due = (ctx.when + timedelta(days=30)).strftime("%B %d, %Y")
    page1 = [
        Line("small", f"{vendor}  |  Accounts Receivable"),
        Line("title", f"Invoice {number}"),
        Line("p", f"Bill to: {N.COMPANY_LONG}, Accounts Payable, 100 Commerce Street, "
                  f"Riverton, TX 79999"),
        Line("p", f"Invoice date: {ctx.when.strftime('%B %d, %Y')}    Terms: Net 30    Due: {due}"),
        Line("p", f"Reference: {title}"),
        Line("p", ""),
        *lines,
    ]  # fmt: skip
    page2 = [
        Line("h1", "Remittance and notes"),
        Line("p", "Please quote the invoice number with your payment. Remittance instructions are "
                  "unchanged from the vendor master on file; we will never change them by email."),
        Line("h2", "Correspondence"),
        Line("p", _wrap_excerpt(ctx.body)),
    ]  # fmt: skip
    return [page1, page2]


_CLAUSES = (
    ("Definitions", "Capitalised terms have the meanings given in the Master Agreement. "
     "\"Business Day\" means a day on which banks are open in Riverton, Texas."),
    ("Term", "This agreement starts on the Effective Date and continues for twelve months, "
     "then month to month until either party gives thirty days' written notice."),
    ("Transactions", "Each transaction is confirmed in writing. If a confirmation is not "
     "disputed within two Business Days it is deemed accepted."),
    ("Price and payment", "Prices are as set out in each confirmation. Invoices are paid by "
     "the 20th day of the month following delivery."),
    ("Credit support", "Either party may request a letter of credit or a guaranty if the "
     "other party's exposure exceeds its unsecured credit limit."),
    ("Confidentiality", "The terms of this agreement are confidential and may be disclosed "
     "only to affiliates, advisers and regulators who need to know them."),
    ("Termination", "On an event of default the non-defaulting party may terminate all "
     "transactions and net the settlement amounts."),
    ("Governing law", "This agreement is governed by the laws of the State of Texas."),
    ("Notices", "Notices go to the addresses in Schedule A, attention: Legal Department."),
)  # fmt: skip


def _contract(ctx: Context, title: str) -> list[list[Line]]:
    party = _org(ctx.seed, ctx.key, 7)
    intro = [
        Line("small", "DRAFT - FOR DISCUSSION PURPOSES ONLY"),
        Line("title", title),
        Line("p", f'between {N.COMPANY_LONG} ("{N.COMPANY}") and {party} ("Counterparty")'),
        Line("p", f"Draft of {ctx.when.strftime('%B %d, %Y')}, circulated by {ctx.sender_name}"),
        Line("p", ""),
        Line("h2", "Background"),
        Line("p", _wrap_excerpt(ctx.body, 500)),
    ]
    clauses = [[Line("h2", f"{i}. {name}"), Line("p", text)]
               for i, (name, text) in enumerate(_CLAUSES, start=1)]  # fmt: skip
    page1 = intro + [x for c in clauses[:3] for x in c]
    page2 = [x for c in clauses[3:7] for x in c]
    page3 = [x for c in clauses[7:] for x in c] + [
        Line("p", ""),
        Line("h2", "Signatures"),
        Line("mono", f"{N.COMPANY_LONG:<36}{party[:36]}"),
        Line("mono", f"{'By: ____________________':<36}By: ____________________"),
        Line("mono", f"{'Name:':<36}Name:"),
        Line("mono", f"{'Title:':<36}Title:"),
    ]
    return [page1, page2, page3]


def _schedule(ctx: Context, title: str) -> list[list[Line]]:
    rows = [
        Line("mono", f"{'Date':<14}{'Time':<10}{'Item':<38}{'Owner':<14}"),
        Line("mono", "-" * 74),
    ]
    items = ("Kick-off and scope", "Data requests due", "Working session", "Draft review",
             "Comments back", "Final review", "Sign-off", "Close-out")  # fmt: skip
    day = ctx.when
    owners = (ctx.sender_name.split()[0], ctx.recipient_name.split()[0], "Finance", "Legal")
    for i, item in enumerate(items):
        day = day + timedelta(days=1 + stable_hash(ctx.seed, ctx.key, "d", i) % 3)
        while day.weekday() >= 5:
            day = day + timedelta(days=1)
        hour = 8 + stable_hash(ctx.seed, ctx.key, "h", i) % 9
        owner = owners[stable_hash(ctx.seed, ctx.key, "o", i) % len(owners)]
        rows.append(
            Line("mono", f"{day.strftime('%a %b %d'):<14}{hour:02d}:00     {item:<38}{owner:<14}")
        )
    page1 = _header(ctx, title, "Schedule") + [Line("h2", "Timetable"), *rows]
    page2 = [
        Line("h1", "Logistics"),
        Line("p", "Meetings are in the 49th floor conference rooms at 100 Commerce Street unless "
                  "noted; dial-in details go out with each calendar invitation."),
        Line("h2", "Context"),
        Line("p", _wrap_excerpt(ctx.body)),
    ]  # fmt: skip
    return [page1, page2]


def _report(ctx: Context, title: str) -> list[list[Line]]:
    metrics = ("Gross margin (USD m)", "Volumes (Bcf/d)", "Power sales (GWh)", "Opex (USD m)",
               "VaR, 95% 1-day (USD m)", "Working capital (USD m)")  # fmt: skip
    rows = [Line("mono", f"{'Metric':<28}{'Actual':>12}{'Plan':>12}{'Variance':>12}"),
            Line("mono", "-" * 64)]  # fmt: skip
    for i, metric in enumerate(metrics):
        plan = _money(ctx.seed, ctx.key, 30 + i, 20, 900) / 10
        actual = plan * (90 + stable_hash(ctx.seed, ctx.key, "a", i) % 21) / 100
        rows.append(
            Line("mono", f"{metric:<28}{actual:>12.1f}{plan:>12.1f}{actual - plan:>+12.1f}")
        )
    regions = ("East", "West", "Central", "Gulf Coast", "Canada")
    detail = [Line("mono", f"{'Region':<16}{'Q-to-date':>12}{'Prior qtr':>12}{'Change':>10}"),
              Line("mono", "-" * 50)]  # fmt: skip
    for i, region in enumerate(regions):
        now = _money(ctx.seed, ctx.key, 50 + i, 5, 120)
        prior = _money(ctx.seed, ctx.key, 60 + i, 5, 120)
        detail.append(Line("mono", f"{region:<16}{now:>12}{prior:>12}{now - prior:>+10}"))
    page1 = _header(ctx, title, "Management report") + [
        Line("h2", "Summary"),
        Line("p", _wrap_excerpt(ctx.body)),
        Line("h2", "Key figures"),
        *rows,
    ]
    page2 = [
        Line("h1", "Detail by region"),
        *detail,
        Line("p", ""),
        Line("h2", "Notes"),
        Line("p", "Figures are preliminary and unaudited. Variances above 10% are explained in the "
                  "commentary distributed with the month-end package."),
        Line("p", "Prior-quarter figures are restated for the current desk structure."),
    ]  # fmt: skip
    return [page1, page2]


def _memo(ctx: Context, title: str) -> list[list[Line]]:
    page1 = [
        Line("small", f"{N.COMPANY_LONG}  |  Internal memorandum"),
        Line("title", title),
        Line("mono", f"To:      {ctx.recipient_name}"),
        Line("mono", f"From:    {ctx.sender_name}, {ctx.sender_role}"),
        Line("mono", f"Date:    {ctx.when.strftime('%B %d, %Y')}"),
        Line("mono", f"Re:      {title}"),
        Line("p", ""),
        Line("h2", "Summary"),
        Line("p", _wrap_excerpt(ctx.body, 900)),
    ]
    page2 = [
        Line("h1", "Next steps"),
        Line("p", "1. Confirm the open points above with the owners named in the email."),
        Line("p", "2. Circulate comments by the end of the week."),
        Line("p", "3. Keep this memo internal; it is not for distribution outside the company."),
    ]
    return [page1, page2]


def _board_deck(ctx: Context, title: str) -> list[list[Line]]:
    low = _money(ctx.seed, ctx.key, 1, 410, 460)
    high = low + _money(ctx.seed, ctx.key, 2, 40, 70)
    ebitda = _money(ctx.seed, ctx.key, 3, 52, 64)
    notice = "BOARD CONFIDENTIAL - not for distribution outside the board and the executive team"
    return [
        [Line("small", notice), Line("title", "Project Larkspur"),
         Line("h1", "Board pre-read: special session"),
         Line("p", f"Prepared for the Board of Directors of {N.COMPANY_LONG}"),
         Line("p", f"{ctx.when.strftime('%B %Y')}  |  Circulated by {ctx.sender_name}, {ctx.sender_role}")],
        [Line("h1", "Agenda"), Line("p", "1. Transaction overview"), Line("p", "2. Strategic rationale"),
         Line("p", "3. Valuation and price range"), Line("p", "4. Financing plan"),
         Line("p", "5. Key risks and mitigations"), Line("p", "6. Timeline and decisions requested")],
        [Line("h1", "1. Transaction overview"),
         Line("p", "Larkspur is a privately held midstream operator with gas gathering, processing "
                   "and storage assets adjacent to our Gulf Coast footprint."),
         Line("p", "Proposed structure: acquisition of 100% of the equity for cash, funded from the "
                   "revolving facility and a new term loan."),
         Line("p", f"Target EBITDA (last twelve months): USD {ebitda} million.")],
        [Line("h1", "2. Strategic rationale"),
         Line("p", "- Adds firm storage capacity that our trading desks currently lease."),
         Line("p", "- Extends the pipeline network to two new industrial demand centres."),
         Line("p", "- Expected annual synergies of USD 9-12 million by year two.")],
        [Line("h1", "3. Valuation and price range"),
         Line("mono", f"{'Method':<34}{'Low (USD m)':>14}{'High (USD m)':>14}"),
         Line("mono", "-" * 62),
         Line("mono", f"{'Trading comparables':<34}{low - 15:>14}{high - 10:>14}"),
         Line("mono", f"{'Precedent transactions':<34}{low + 5:>14}{high + 15:>14}"),
         Line("mono", f"{'Discounted cash flow':<34}{low:>14}{high:>14}"),
         Line("p", ""),
         Line("p", f"Recommended price range for the first offer: USD {low}-{high} million.")],
        [Line("h1", "4. Financing plan"),
         Line("p", "Cash consideration funded by USD 250 million from the revolver and a new "
                   "five-year term loan; pro forma leverage stays below 3.0x."),
         Line("p", "No equity issuance is required. Rating agencies to be briefed after signing.")],
        [Line("h1", "5. Key risks and mitigations"),
         Line("p", "- Regulatory approval of the storage assets: early filing, no overlap issues expected."),
         Line("p", "- Integration of control-room systems: twelve-month plan owned by Operations."),
         Line("p", "- Leak of the process: advisers are under NDA; no contact with the target's "
                   "lenders until the board approves the offer.")],
        [Line("h1", "6. Timeline and decisions requested"),
         Line("p", "Thursday: board approval of the price range and the non-binding offer."),
         Line("p", "Following two weeks: confirmatory due diligence; signing targeted within six weeks."),
         Line("p", ""),
         Line("small", notice)],
    ]  # fmt: skip


def _roster(ctx: Context, title: str) -> list[list[Line]]:
    from mailsim.importers.leadership import ROSTER  # the company's own people

    rows = [Line("mono", f"{'Name':<22}{'Title':<44}{'Ext.':>6}"), Line("mono", "-" * 72)]
    for person in (p for p in ROSTER if p.group in ("exec", "director", "assistant")):
        ext = f"x55{(stable_hash(ctx.seed, 'ext', person.id) % 100):02d}"
        rows.append(Line("mono", f"{person.name:<22}{person.role[:43]:<44}{ext:>6}"))
    page1 = _header(ctx, title, "Contact list") + rows
    page2 = [
        Line("h1", "Notes"),
        Line("p", "Print on legal-size paper. Please send changes to the Office of the CEO."),
        Line("h2", "Covering note"),
        Line("p", _wrap_excerpt(ctx.body)),
    ]
    return [page1, page2]


_BUILDERS = {
    "credit_watch": _credit_watch, "invoice": _invoice, "contract": _contract,
    "schedule": _schedule, "report": _report, "memo": _memo, "board_deck": _board_deck,
    "roster": _roster,
}  # fmt: skip


def _spreadsheet(ctx: Context, title: str) -> bytes:
    rows: list[list[str | float | int]] = [[title], [f"Prepared by {ctx.sender_name}",
                                                     ctx.when.strftime("%Y-%m-%d")], []]  # fmt: skip
    rows.append(["Item", "Counterparty", "Volume", "Price", "Amount"])
    for i in range(16):
        vol = _money(ctx.seed, ctx.key, 70 + i, 1, 50) * 1000
        price = _money(ctx.seed, ctx.key, 90 + i, 180, 640) / 100
        rows.append([f"Line {i + 1}", _org(ctx.seed, ctx.key, 200 + i), vol, price,
                     round(vol * price, 2)])  # fmt: skip
    return xlsx_bytes(rows, sheet=title[:31] or "Sheet1")


def build(kind: str, ctx: Context, *, filename_title: str | None = None) -> Doc:
    """The attachment of `kind` for one message."""
    title = filename_title or _title(ctx.subject)
    stamp = ctx.when.strftime("%Y-%m-%d")
    if kind == "spreadsheet":
        return Doc(f"{_slug(title)}.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   _spreadsheet(ctx, title))  # fmt: skip
    pages = _BUILDERS[kind](ctx, title)
    data = pdf_bytes(pages, title=title, author=ctx.sender_name,
                     created=ctx.when.strftime("%Y%m%d%H%M%S"),
                     footer=f"{N.COMPANY_LONG} - {title} - {stamp}")  # fmt: skip
    name = f"{_slug(title)}.pdf" if kind == "board_deck" else f"{_slug(title)}-{stamp}.pdf"
    return Doc(name, "application/pdf", data)
