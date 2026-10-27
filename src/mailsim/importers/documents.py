"""Attachment documents for generated leadership mail.

A message whose body says something is attached ("Attached is ...", "Enclosed ...",
"<<file.doc>>", a document title on a line of its own, "in .pdf format") gets a real document:
a multi-page PDF (or an XLSX for spreadsheets) whose title, dates, parties and figures follow
the email. Kinds: board pre-read deck, presentation, credit watch list, invoice, draft
agreement, schedule, report / financial summary, spreadsheet, contact list and memo. The kind
comes from the attachment's own name or the sentence that announces it before the rest of the
mail; the title from the stub, the announced noun phrase or a title-like subject. Documents
carry the sender's organisation: only company senders produce company-internal documents.
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
    r"|<<[^<>\n]+>>|\bhere (?:is|are) (?:the|a|my|our|two|three|four|both) (?:latest |revised |"
    r"updated |final |new |draft |current )*(?:\w+ )?(?:version|draft|copy|list|file|spreadsheet|"
    r"report|schedule|memo|letter|presentation|document|summary|agenda|slides|numbers|figures|"
    r"outline|proposal|requests|forms?|notes|plan|deck|worksheet|charts?)\b",
    re.IGNORECASE,
)
# Lotus/Outlook attachment stubs left in bodies (" - report.doc", "<<x5503.DOC>>")
_EXT = r"(?:docx?|xlsx?|xls|pdf|pptx?|txt|zip|csv|wpd|rtf|url)"
STUB_LINE = re.compile(
    rf"(?im)^[ \t]*(?:-[ \t]+)?(?:<<[^<>]{{1,120}}>>|[\w.()\-][\w.()\- ]{{0,80}}\.{_EXT}|"
    rf"\(See attached file: [^()\n]{{1,120}}\))[ \t]*$\n?"
)
INLINE_STUB = re.compile(r"[ \t]*<<[^<>]{1,120}>>")
_DOC_NOUN = (
    r"(?:agreement|contract|report|schedule|agenda|minutes|memo(?:randum)?|letter|list|"
    r"spreadsheet|worksheet|workbook|presentation|slides|deck|proposal|draft|summary|analysis|"
    r"forecast|budget|invoice|form|notice|outline|statement|table|model|calendar|itinerary|"
    r"resolution|confirmation|term sheet|packet|policy|procedures?|plan|review|notes|"
    r"calculations?|template|guidelines|questionnaire|brochure|newsletter|issue|assessments?|"
    r"requests?|q&a|faq|charts?|exhibits?|matrix|file)"
)
# a document title alone on its own line, after the text ("Credit Worksheet", "Threshold Table")
TITLE_STUB = re.compile(
    rf"(?im)(?<=\n\n)[ \t]*((?:[A-Z][\w&'/-]*[ \t]+){{0,4}}{_DOC_NOUN})[ \t]*$(?=\n\n|\n?\Z)"
)
# other ways a mail says a document comes with it (used with ATTACH_CLAIM and TITLE_STUB)
ATTACH_HINT = re.compile(
    r"(?m)^[ \t]*attached\b|(?<=[.!?] )attached\b|\bhere's (?:the |a |my |our |an )|"
    r"\b(?:this|here) is the (?:latest |revised |final |draft |new )?(?:[\w&'-]+ ){0,3}?"
    r"(?:contract|agreement|letter|memo|report|list|spreadsheet|worksheet|schedule|form|draft)\b|"
    r"\b(?:in|as an?)\s+\.?(?:pdf|word|excel|powerpoint|ppt|xls)\s+(?:format|file|document|"
    r"attachment|version)\b|\bprint (?:all|the|both) (?:\w+ )?(?:documents|files|pages)\b|"
    r"\bthe following (?:documents?|files?)\b|\b(?:see|find|review) the (?:enclosed |following )?"
    r"(?:document|file|spreadsheet|worksheet|deck|slides|presentation)s?\b(?! (?:on|at|in) )",
    re.IGNORECASE,
)
# the noun phrase a claim announces: "attached is the revised Precedent Agreement"
_CLAIM_NP = re.compile(
    rf"(?:attached (?:is|are|please find|you will find|for your review is|herewith is)|enclosed "
    rf"(?:is|are)|i(?:'ve| have)? attached|i attach|i am attaching|i'm attaching|attaching|"
    rf"please (?:find|see) (?:the )?attached|here (?:is|are))\s+(?:a |an |the |my |our |your |"
    rf"this |two |three )?(?:(?:copy|draft|version) of (?:the |a |our |my )?)?"
    rf"(?P<np>(?:[\w&'/-]+ ){{0,5}}?{_DOC_NOUN}s?)\b",
    re.IGNORECASE,
)


def trailing_titles(body: str) -> tuple[str, list[str]]:
    """Title-only lines after the text ("Credit Worksheet", "Threshold Table") name attached
    files: they are removed and returned as titles. A title line followed by more text is a
    heading and stays."""
    titles: list[str] = []
    out = body
    for m in reversed(list(TITLE_STUB.finditer(body))):
        rest = TITLE_STUB.sub("", body[m.end() :])
        if len(re.findall(r"[A-Za-z]{2,}", re.sub(r"\{\{[^{}]*\}\}", " ", rest))) > 3:
            break  # more text follows: a heading
        titles.insert(0, _title(m.group(1)))
        out = out[: m.start()] + out[m.end() :]
    return out, titles


def claims_attachment(body: str) -> bool:
    """The body says that a document comes with the mail (claim, stub, title line, hint)."""
    return bool(
        (ATTACH_CLAIM.search(body)
         and not re.search(r"(?i)attached (?:is|are) (?:a |the )?(?:link|url)", body))
        or STUB_LINE.search(body) or INLINE_STUB.search(body) or TITLE_STUB.search(body)
        or ATTACH_HINT.search(body)
    )  # fmt: skip


def stub_title(stub: str) -> str | None:
    """A readable title from an attachment stub ("<<Comparison of Turbine Agreement.DOC>>");
    None for file names that are not words ("PrecedentnewJan_Corvane.doc", "x5503.DOC")."""
    name = re.sub(
        r"^[\s<-]+|[\s>]+$", "", re.sub(r"(?i)^\s*\(see attached file:\s*|\)\s*$", "", stub)
    )
    stem = re.sub(rf"\.{_EXT}$", "", re.sub(r"\s+", " ", name), flags=re.I)
    stem = re.sub(r"[_\-.]+", " ", stem)
    words = stem.split()
    if any(re.search(r"[a-z][A-Z]|\d|^[a-z]+[A-Z]", w) for w in words):
        return None  # camel-case joins and numbered names are not titles
    words = [w for w in words if re.fullmatch(r"[A-Za-z][A-Za-z&']*", w)]
    if len([w for w in words if len(w) >= 4]) < 2:
        return None
    return _title(" ".join(words))


_PRONOUN_OR_VERB = re.compile(
    r"\b(?:i|we|you|he|she|they|it|is|are|was|were|has|have|had|will|would|can|could|should|"
    r"please|let'?s|here|it's|i'm|i've|we're|don't|didn't|did|do|does|been|be|my|your|our|me|us|"
    r"this|that|there|what|who|why|how|if|when|just|also|thanks|thank|hi|hello|fyi)\b",
    re.IGNORECASE,
)


def title_like(subject: str) -> bool:
    """A subject that names a thing ("Precedent Agreement", "Q3 Budget Review"), not a sentence
    fragment ("E-mail has been a bit problematic here")."""
    s = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", subject, flags=re.I).strip(" .:-")
    words = s.split()
    return 1 <= len(words) <= 8 and not _PRONOUN_OR_VERB.search(s) and not s.endswith("?")


_DOC_WORD = re.compile(rf"(?i)^{_DOC_NOUN}s?$")
_LEADING = re.compile(r"(?i)^(?:the|a|an|our|my|your|their|this|these|two|three|latest|revised|"
                      r"updated|final|new|current|rough|following|attached|enclosed|copy|draft|"
                      r"version|of|most|recent|cut|at)$")  # fmt: skip


_GENERIC_NAME = frozenset(["file", "files", "document", "documents", "draft", "copy", "version",
                           "request", "requests", "attachment", "list", "notes"])  # fmt: skip


def announced_name(body: str) -> str | None:
    """The document an attachment claim names, as a title: "attached is the revised Precedent
    Agreement with ..." -> "Precedent Agreement", "a copy of our latest draft Master Power
    Purchase and Sale Agreement" -> "Master Power Purchase and Sale Agreement", "the attached
    credit worksheet" -> "Credit Worksheet". None when the claim names nothing specific."""
    m = _CLAIM_NP.search(body) or ATTACH_CLAIM.search(body) or ATTACH_HINT.search(body)
    if m is None:
        return None
    text = body[m.start() : m.start() + 400].replace("\n", " ")
    text = re.split(r"(?<=[A-Za-z0-9)])[.!?](?:\s|$)", text, maxsplit=1)[0]
    text = re.sub(r"\([^)]*\)?", " ", text)
    text = re.sub(r"(?i)\b(?:per|at) (?:your|my|our|his|her) request\b", " ", text)
    raw = text.split()
    words = [w.strip(",;:'\"") for w in raw]
    first = next((i for i, w in enumerate(words) if _DOC_WORD.match(w)), None)
    if first is None:
        return None
    end = first
    i = first
    while not raw[i].endswith(",") and i + 1 < min(len(words), first + 8):
        i += 1
        w = words[i]
        if _DOC_WORD.match(w) or (w[:1].isupper() and w.replace("&", "").isalnum()):
            end = i
        elif w not in ("and", "&", "of"):
            break
    start = first
    if words[first][:1].isupper():
        while start > 0 and not raw[start - 1].endswith(",") and not _LEADING.match(
            words[start - 1]
        ) and (words[start - 1][:1].isupper() or (
            words[start - 1] in ("and", "&") and start > 1 and words[start - 2][:1].isupper()
        )):  # fmt: skip
            start -= 1  # "Master Power Purchase and Sale Agreement"
    else:  # lower-case qualifiers: "credit worksheet", "sale and purchase contract"
        taken = 0
        while start > 0 and taken < 4 and not raw[start - 1].endswith(",") and (
            words[start - 1] in ("and", "&") or (
                words[start - 1].isalpha() and words[start - 1].islower()
                and len(words[start - 1]) >= 3 and not _LEADING.match(words[start - 1])
                and not _PRONOUN_OR_VERB.search(words[start - 1]))
        ):  # fmt: skip
            start -= 1
            taken += 1
        while start < first and words[start] in ("and", "&"):
            start += 1
    phrase = words[start : end + 1]
    while phrase and _LEADING.match(phrase[0]):
        phrase.pop(0)
    while phrase and phrase[-1] in ("and", "&", "of"):
        phrase.pop()
    if not phrase or _PRONOUN_OR_VERB.search(" ".join(phrase)):
        return None
    if len(phrase) == 1 and phrase[0].lower() in _GENERIC_NAME:
        return None
    return _title(" ".join(phrase))


def _names_a_document(part: str) -> bool:
    """A listed item that is a noun phrase naming a document ("the minutes", "Budget"), not a
    clause that follows the list ("pls review and share comments")."""
    words = [w.strip(",;:'\"") for w in part.split()]
    if not words or not any(_DOC_WORD.match(w) for w in words):
        return False
    return bool(_LEADING.match(words[0]) or _DOC_WORD.match(words[0]) or words[0][:1].isupper())


def announced_names(body: str) -> list[str]:
    """Every document an attachment claim lists ("I attach an agenda, minutes and a copy of the
    sale and purchase contract" -> Agenda, Minutes, Sale and Purchase Contract), at most three."""
    m = _CLAIM_NP.search(body) or ATTACH_CLAIM.search(body)
    if m is None:
        return []
    sentence = re.split(r"(?<=[A-Za-z0-9)])[.!?](?:\s|$)", body[m.start() : m.start() + 400])[0]
    sentence = sentence.replace("\n", " ")
    parts = re.split(r",\s*|\s+and\s+(?=(?:an?|the|my|our|a copy)\b)|\s+as well as\s+", sentence)
    parts = [p for i, p in enumerate(parts) if i == 0 or _names_a_document(p)]
    names = [announced_name(p if i == 0 else f"Attached is {p}") for i, p in enumerate(parts)]
    return list(dict.fromkeys(n for n in names if n))[:3]


def doc_title(kind: str, subject: str, body: str, *, subject_is_title: bool,
              stub: str | None = None) -> str:  # fmt: skip
    """The document's title: a clean stub name, the noun phrase a claim announces, a title-like
    original subject, or the kind's generic title."""
    if stub:
        return stub
    np = announced_name(body)
    if np:
        return np
    if subject_is_title and title_like(subject):
        return _title(subject)
    return KIND_TITLES.get(kind, "Document")


KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credit_watch", re.compile(r"credit watch|watch ?list|hot ?list", re.I)),
    ("presentation", re.compile(r"\b(?:slides?|presentation|deck|powerpoint|pptx?)\b", re.I)),
    ("roster", re.compile(r"\b(?:committee|contact|distribution|phone|member|attendee|team) list\b",
                          re.I)),
    ("invoice", re.compile(r"\binvoices?\b|\bbilling\b", re.I)),
    ("spreadsheet", re.compile(r"\b(?:spreadsheets?|excel|xls|worksheets?|workbooks?|"
                               r"threshold table|matrix|rotations)\b", re.I)),
    ("contract", re.compile(r"\b(?:agreements?|contracts?|(?<!re-)confirm(?:ation)?s?|term sheet|"
                            r"amendment|lc form|letter of credit|guarant\w+|tariff|rate schedule|"
                            r"gisb|naesb|isda|master)\b", re.I)),
    ("agenda", re.compile(r"\bagendas?\b", re.I)),
    ("schedule", re.compile(r"\b(?:schedule|calendar|itinerary|timeline|timetable)\b",
                            re.I)),
    ("memo", re.compile(r"\b(?:memo|memorandum|minutes|notes|outline|proposal|letter|notice|"
                        r"packet|policy|procedures?|guidelines|newsletter|issue|advisory|forms?|"
                        r"q&a|faq|assessments?|requests?|charts?|drafts?)\b", re.I)),
    ("report", re.compile(r"\b(?:report|summary|analysis|forecast|budget|p&l|statement|results|"
                          r"position|listing|survey|study)\b", re.I)),
)  # fmt: skip
KIND_TITLES = {
    "agenda": "Agenda",
    "credit_watch": "Credit Watch List", "presentation": "Presentation", "roster": "Contact List",
    "invoice": "Invoice", "spreadsheet": "Workbook", "contract": "Draft Agreement",
    "schedule": "Schedule", "memo": "Memo", "report": "Summary Report",
    "board_deck": "Board Pre-read",
}  # fmt: skip


@dataclass(frozen=True)
class Doc:
    filename: str
    content_type: str
    data: bytes


def kind_of(text: str, *, focus: str = "") -> str:
    """The document kind: from `focus` (the attachment's name or the sentence announcing it)
    first, then from the whole text."""
    for source in (focus, text):
        for kind, rx in KINDS:
            if source and rx.search(source):
                return kind
    return "memo"


def claim_sentence(body: str) -> str:
    """The sentence that announces the attachment (empty if none)."""
    for rx in (_CLAIM_NP, ATTACH_CLAIM, ATTACH_HINT):
        m = rx.search(body)
        if m:
            start = max(body.rfind(".", 0, m.start()), body.rfind("\n", 0, m.start())) + 1
            end_m = re.search(r"[.!?\n]", body[m.end() :])
            stop = m.end() + (end_m.end() if end_m else len(body) - m.end())
            return body[start:stop].strip()
    return ""


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
    org: str = N.COMPANY_LONG  # the sender's organisation (letterhead, footer)
    internal: bool = True  # the sender works for the company
    roster: tuple[tuple[str, str, str], ...] = ()  # company people: (id, name, title)


def _header(ctx: Context, title: str, label: str) -> list[Line]:
    return [
        Line("small", f"{ctx.org}  |  {label}" if ctx.org else label),
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
    page1 = _header(ctx, title, "Credit Risk Management" if ctx.internal else "Credit") + [
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
    vendor = _org(ctx.seed, ctx.key, 99) if ctx.internal or not ctx.org else ctx.org
    number = f"INV-{ctx.when.year}-{_money(ctx.seed, ctx.key, 0, 1000, 9999)}"
    items: tuple[str, ...] = ("Transportation charges", "Storage fees", "Balancing charges",
                              "Service fee", "Late payment interest", "Administrative fee")  # fmt: skip
    service = re.match(r"(?i)^(?:(?:re|fw|fwd):\s*)*invoices? (?:for|re:?)\s+(.{3,40})$",
                       ctx.subject.strip())  # fmt: skip
    if service:  # "Invoice for advisory work ... three-month trial period": what the mail says
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        span = re.search(r"(?i)\b(one|two|three|four|five|six|\d)[- ]months?\b", ctx.body)
        n = (int(span.group(1)) if span.group(1).isdigit() else words[span.group(1).lower()]
             ) if span else 3  # fmt: skip
        name = service.group(1).strip(" .")
        items = tuple(f"{name[:1].upper() + name[1:]} - month {i + 1}" for i in range(min(n, 6)))
    lines = [Line("mono", f"{'Description':<40}{'Qty':>8}{'Amount (USD)':>18}"),
             Line("mono", "-" * 66)]  # fmt: skip
    total = 0
    count = len(items) if service else 3 + stable_hash(ctx.seed, ctx.key, "n") % 3
    for i, item in enumerate(items[:count]):
        qty = 1 if service else _money(ctx.seed, ctx.key, 10 + i, 1, 30)
        amount = _money(ctx.seed, ctx.key, 20 + i, 400, 9000) * qty
        total += amount
        lines.append(Line("mono", f"{item:<40}{qty:>8}{amount:>18,.2f}"))
    lines += [Line("mono", "-" * 66), Line("mono", f"{'Total due':<48}{total:>18,.2f}")]
    due = (ctx.when + timedelta(days=30)).strftime("%B %d, %Y")
    page1 = [
        Line("small", f"{vendor}  |  Accounts Receivable"),
        Line("title", f"Invoice {number}"),
        Line("p", f"Bill to: {N.COMPANY_LONG}, Accounts Payable, {N.FAKE_ADDRESS}"),
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
    # a counterparty sends its own draft; company staff and outside counsel send one with a
    # fictional counterparty
    own = not ctx.internal and ctx.org and "LLP" not in ctx.org
    party = ctx.org if own else _org(ctx.seed, ctx.key, 7)
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
    person = not ctx.org or ctx.org != ctx.sender_name  # a newsletter is not a person
    owners = ((ctx.sender_name.split()[0] if person else "Organizers"),
              ctx.recipient_name.split()[0], "Finance", "Legal")  # fmt: skip
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
    where = (f"the 49th floor conference rooms at {N.FAKE_STREET}" if ctx.internal
             else "the board room or by video call" if ctx.org == "Board of Directors"
             else f"the offices of {ctx.org or ctx.sender_name} or by video call")  # fmt: skip
    page2 = [
        Line("h1", "Logistics"),
        Line("p", f"Meetings are in {where} unless noted; dial-in details go out with each "
                  "calendar invitation."),
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
    label = "Internal memorandum" if ctx.internal else "Memorandum"
    page1 = [
        Line("small", f"{ctx.org}  |  {label}" if ctx.org else label),
        Line("title", title),
        Line("mono", f"To:      {ctx.recipient_name}"),
        Line("mono", f"From:    {ctx.sender_name}, {ctx.sender_role}"),
        Line("mono", f"Date:    {ctx.when.strftime('%B %d, %Y')}"),
        Line("mono", f"Re:      {title}"),
        Line("p", ""),
        Line("h2", "Summary"),
        Line("p", _wrap_excerpt(ctx.body, 900)),
    ]
    keep = ("3. Keep this memo internal; it is not for distribution outside the company."
            if ctx.internal else f"3. Questions to {ctx.sender_name}.")  # fmt: skip
    page2 = [
        Line("h1", "Next steps"),
        Line("p", "1. Confirm the open points above with the owners named in the email."),
        Line("p", "2. Circulate comments by the end of the week."),
        Line("p", keep),
    ]
    return [page1, page2]


def _agenda(ctx: Context, title: str) -> list[list[Line]]:
    """A meeting agenda: numbered items with owners and times (not a project timetable)."""
    topics = ("Welcome and introductions", "Review of open action items", "Commercial update",
              "Contract and documentation status", "Risk and credit items",
              "Next steps and date of the next meeting")  # fmt: skip
    owners = (ctx.sender_name.split()[0], ctx.recipient_name.split()[0], "All")
    start = 9 * 60 + 30 * (stable_hash(ctx.seed, ctx.key, "agenda") % 8)
    rows = []
    for i, topic in enumerate(topics, start=1):
        at = start + 20 * (i - 1)
        owner = owners[stable_hash(ctx.seed, ctx.key, "own", i) % len(owners)]
        rows.append(Line("p", f"{i}.  {at // 60:02d}:{at % 60:02d}  {topic}  ({owner})"))
    return [[Line("small", ctx.org or ctx.sender_name), Line("title", title),
             Line("p", f"Meeting of {ctx.when.strftime('%B %d, %Y').replace(' 0', ' ')}"),
             Line("p", ""), *rows, Line("p", ""),
             Line("p", "Papers for the items above circulate with this agenda.")],
            [Line("h1", "Background"), Line("p", _wrap_excerpt(ctx.body)),
             Line("p", f"Questions on the agenda: {ctx.sender_name}.")]]  # fmt: skip


def _presentation(ctx: Context, title: str) -> list[list[Line]]:
    """A slide deck: one topic per page, bullet points from seeded figures."""
    growth = _money(ctx.seed, ctx.key, 1, 2, 14)
    margin = _money(ctx.seed, ctx.key, 2, 18, 41)
    org = ctx.org or ctx.sender_name
    return [
        [Line("small", org), Line("title", title),
         Line("p", f"{ctx.when.strftime('%B %Y')}  |  {ctx.sender_name}, {ctx.sender_role}")],
        [Line("h1", "Context"), Line("p", _wrap_excerpt(ctx.body, 500))],
        [Line("h1", "Market view"),
         Line("p", f"- Demand growth of {growth}% a year in the target region."),
         Line("p", f"- Gross margins of {margin}% on structured products."),
         Line("p", "- Regulatory approval expected within two quarters.")],
        [Line("h1", "Proposal"),
         Line("p", "- Phase 1: scope and data room (four weeks)."),
         Line("p", "- Phase 2: pricing and term sheet (six weeks)."),
         Line("p", "- Phase 3: documentation and close.")],
        [Line("h1", "Next steps"), Line("p", "Comments on this draft by the end of the week."),
         Line("small", f"Draft for discussion - {org}")],
    ]  # fmt: skip


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
    rows = [Line("mono", f"{'Name':<22}{'Title':<44}{'Ext.':>6}"), Line("mono", "-" * 72)]
    for pid, name, role in ctx.roster:
        ext = f"x55{(stable_hash(ctx.seed, 'ext', pid) % 100):02d}"
        rows.append(Line("mono", f"{name:<22}{role[:43]:<44}{ext:>6}"))
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
    "roster": _roster, "presentation": _presentation, "agenda": _agenda,
}  # fmt: skip


_PEOPLE_SHEET = re.compile(r"\b(?:rotations?|participants?|staff|people|employees|roster|"
                           r"attendees|headcount|team members)\b", re.I)  # fmt: skip


def _spreadsheet(ctx: Context, title: str) -> bytes:
    rows: list[list[str | float | int]] = [[title], [f"Prepared by {ctx.sender_name}",
                                                     ctx.when.strftime("%Y-%m-%d")], []]  # fmt: skip
    if _PEOPLE_SHEET.search(title + " " + ctx.body):  # a list of people, not of trades
        rows.append(["Name", "Group", "Current assignment", "Next assignment", "Start"])
        groups = ("Gas Trading", "Power Trading", "Structuring", "Risk", "Origination")
        for i in range(12):
            h = stable_hash(ctx.seed, ctx.key, "person", i)
            name = (
                f"{N.FAKE_FIRST[h % len(N.FAKE_FIRST)]} {N.FAKE_LAST[(h >> 8) % len(N.FAKE_LAST)]}"
            )
            start = (ctx.when + timedelta(days=30 * (1 + (h >> 16) % 6))).strftime("%Y-%m")
            rows.append([name, groups[(h >> 4) % 5], groups[(h >> 12) % 5], groups[(h >> 20) % 5],
                         start])  # fmt: skip
        return xlsx_bytes(rows, sheet=title[:31] or "Sheet1")
    rows.append(["Item", "Counterparty", "Volume", "Price", "Amount"])
    for i in range(16):
        vol = _money(ctx.seed, ctx.key, 70 + i, 1, 50) * 1000
        price = _money(ctx.seed, ctx.key, 90 + i, 180, 640) / 100
        rows.append([f"Line {i + 1}", _org(ctx.seed, ctx.key, 200 + i), vol, price,
                     round(vol * price, 2)])  # fmt: skip
    return xlsx_bytes(rows, sheet=title[:31] or "Sheet1")


def build(kind: str, ctx: Context, *, filename_title: str | None = None,
          filename: str | None = None) -> Doc:  # fmt: skip
    """The attachment of `kind` for one message (`filename_title`: the title, else the
    subject's; `filename`: an exact file name)."""
    if kind == "roster" and not (ctx.internal and ctx.roster):
        kind = "memo"  # only the company's own staff circulate its contact list
    title = filename_title or _title(ctx.subject)
    stamp = ctx.when.strftime("%Y-%m-%d")
    if kind == "spreadsheet":
        return Doc(f"{_slug(title)}.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   _spreadsheet(ctx, title))  # fmt: skip
    pages = _BUILDERS[kind](ctx, title)
    data = pdf_bytes(pages, title=title, author=ctx.sender_name,
                     created=ctx.when.strftime("%Y%m%d%H%M%S"),
                     footer=f"{ctx.org or ctx.sender_name} - {title} - {stamp}",
                     producer=f"{ctx.org or ctx.sender_name} Document Services")  # fmt: skip
    name = filename or (f"{_slug(title)}.pdf" if kind == "board_deck"
                        else f"{_slug(title)}-{stamp}.pdf")  # fmt: skip
    return Doc(name, "application/pdf", data)
