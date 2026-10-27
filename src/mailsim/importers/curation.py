"""Curation rules for corpus-derived leadership mail: what a record must not contain, whether
its content fits the persona it is bound to, and the message frame (greeting, sign-off,
signature block, subject) rewritten for the bound personas.

All functions work on templates (placeholders ``{{p:12:n}}`` still in place) or on rendered
text, never on raw corpus text.
"""

from __future__ import annotations

import re

from mailsim.importers import names as N
from mailsim.importers.documents import STUB_LINE

_PH = r"\{\{[pqsxol]:\d+:[nfle]\}\}"
PERSON_PH = re.compile(r"\{\{([pqs]:\d+):[nfl]\}\}")

# ------------------------------------------------------------------ content markers
# Word lists per category (regular-expression fragments, matched case-insensitively at word
# boundaries unless noted); `category(rx, text)` names the category that matched.


def _categories(words: dict[str, tuple[str, ...]], flags: int = re.IGNORECASE) -> re.Pattern[str]:
    def bound(w: str) -> str:  # a word boundary before fragments that start with a word
        return rf"\b{w}" if re.match(r"[\w(]", w) else w

    return re.compile(
        "|".join(rf"(?P<{cat}>{'|'.join(bound(w) for w in ws)})" for cat, ws in words.items()),
        flags,
    )


def category(rx: re.Pattern[str], text: str) -> str | None:
    """The category (group name) of the first match of a `_categories` pattern."""
    m = rx.search(text)
    return m.lastgroup if m else None


# Private or sensitive content: never curated into a pack.
SENSITIVE_WORDS: dict[str, tuple[str, ...]] = {
    "credentials": (
        r"password",
        r"passwd",
        r"social security",
        r"ssn\b",
        r"credit card",
        r"401\(?k\)?",
        r"rrsp\b",
        r"retirement savings",
        r"access shut off",
        r"not on list",
    ),
    "health": (
        r"diagnos\w*",
        r"cancer",
        r"pregnan\w*",
        r"hospital\w*",
        r"surgery",
        r"medical",
        r"illness",
        r"doctor\w*",
        r"vaccin\w*",
        r"flu shots?",
        r"died\b",
        r"death\b",
        r"funeral",
        r"baby\b",
        r"birth of",
        r"medicaid",
    ),
    "private_life": (
        r"divorce",
        r"wife\b",
        r"husband",
        r"my (?:son|daughter|kids)\b",
        r"girlfriend",
        r"boyfriend",
        r"dating\b",
        r"love you",
        r"miss you",
        r"my parents",
        r"diamonds?\b",
        r"god\b",
        r"religio\w*",
        r"sex\w*",
        r"porn\w*",
        r"nude\b",
        r"drunk",
        r"drinking",
        r"hangover",
        r"hung over",
        r"booze",
        r"sober\b",
        r"doing shots",
        r"duchess",
        r"best pick",
        r"got out of my",
        r"church",
        r"prayers?\b",
        r"pray\b",
        r"shit\b",
        r"damn\b",
        r"crap\b",
        r"fuck\w*",
        r"hell\b",
        r"screwing",
        r"therapist",
        r"speech/language",
        r"reading readiness",
        r"teacher\b",
    ),
    "employment": (
        r"lay-?offs?\b",
        r"laid off",
        r"terminated",
        r"terminate\b",
        r"firing\b",
        r"fired\b",
        r"severance",
        r"resign\w*",
        r"promotions?\b",
        r"stock options?",
        r"grant price",
        r"retention (?:payments?|bonus\w*)",
        r"work performance",
        r"disciplin\w*",
        r"poor at best",
        r"bonus\w*",
        r"headcount",
        r"human resources rep",
        r"offer/contract from",
        r"accepted an offer",
        r"reorganization",
        r"harass\w*",
        r"enhance (?:luncheon|program)",
        r"pay ?checks?",
        r"payroll",
        r"direct deposits?",
        r"paid semi-monthly",
        r"pay stub",
        r"imputed income",
        r"taxes withheld",
        r"tax liabilit\w*",
        r"out sick",
        r"was sick",
        r"expense account",
        r"salary",
        r"salaries",
        r"relocation",
        r"housing up to",
        r"pocket-book",
        r"benefits info",
        r"visa\b",
        r"deferred compensation",
        r"compensation team",
    ),
    "legal_trouble": (
        r"arrest\w*",
        r"lawsuit against",
        r"judge [A-Z{]",
        r"bankruptcy court",
        r"insider trading",
        r"good buy",
        r"buy the stock",
        r"stock is up",
        r"already up \d",
        r"civil penalty",
    ),
    "company_crisis": (
        r"bankrupt\w*",
        r"chapter 11",
        r"cpt\.? 11",
        r"ch\.? 11\b",
        r"creditors",
        r"post[- ]?petition",
        r"pre-?petition",
        r"takeovers?\b",
        r"our stock",
        r"recent events",
        r"recent developments",
        r"cve\b",
        r"the estate\b",
        r"netco\b",
        r"if we default",
        r"leak (?:about|runs)",
        r"possible sale",
        r"redeployment",
        r"discrimination",
        r"the picks\b",
        r"published a book",
    ),
    "politics_news": (
        r"tax cuts?\b",
        r"governor",
        r"gov\.",
        r"cnn\b",
        r"senate\b",
        r"senators?\b",
        r"assembly(?:man|woman)?\b",
        r"congressm[ae]n",
        r"legislature",
        r"the articles\b",
        r"coalition for",
        r"[as]bx? ?\d+\b",
    ),
    "personal_finance": (r"my investment", r"financial advis[eo]r"),
}
SENSITIVE = _categories(SENSITIVE_WORDS)
# Mass mail and automated notices: only the newsletter persona sends such text.
AUTOMATED_WORDS: dict[str, tuple[str, ...]] = {
    "notification": (
        r"automated (?:e-?mail|message|notification)",
        r"do not reply",
        r"please do not respond",
        r"the report named",
        r"is now available for viewing",
        r"this e-?mail was sent",
        r"this message was sent",
        r"auto-?reply",
        r"out of the office",
        r"automatically notified",
        r"automatic notification",
        r"urgent notification",
        r"alert posted",
        r"calendar entry",
        r"request id",
        r"expense report",
        r"your net pay",
        r"has just sent you",
        r"pickup window",
        r"e-?card",
        r"you don't need to reply",
        r"this is to remind you",
        r"synchroniz\w+ (?:mailbox|folder)",
        r"delivery status notification",
        r"automatically generated",
    ),
    "mass_mail": (
        r"dear [\w ]{0,40}customers?\b",
        r"click here",
        r"unsubscribe",
        r"to be removed",
        r"mailing list",
        r"you are receiving this",
        r"subscribers?\b",
        r"survey\b",
        r"market participants",
        r"all employees",
        r"dear colleagues",
        r"ladies and gentlemen",
        r"to all\b",
        r"all corvane\b",
        r"distribution list",
        r"to whom it may concern",
        r"click on (?:this|the following|the) link",
        r"click on the url",
        r"discussion list",
        r"information contained herein",
    ),
    "announcement": (
        r"we (?:are pleased to |regret to )?announce",
        r"with (?:deep )?regret",
        r"effective immediately,? reporting",
        r"reporting responsibility",
        r"will report directly to",
        r"the following changes will be made",
        r"as you may have noticed",
        r"please note the correction",
        r"your assistance is (?:requested|appreciated)",
        r"single entry point",
        r"(?:fond )?farewell",
        r"please join us",
        r"thank you for taking the time",
        r"i want everyone",
        r"this distribution\b",
        r"sent to you in error",
        r"to add additional people",
        r"performed maintenance",
        r"scheduled maintenance",
        r"voicemail",
        r"orientation\b",
        r"we encourage all of you",
        r"registration will be",
        r"has moved (?:from|to) the",
        r"management asks",
        r"sent automatically",
        r"mailbox is approaching",
        r"thank you for your cooperation",
        r"described in this memo",
        r"as many of you know",
        r"office of the chairman",
        r"brown ?bag",
        r"this is a notification",
        r"no signature is required",
        r"sincerely apologize",
        r"will be announced shortly",
        r"townhall live",
        r"pre-submit your questions",
        r"also in this issue",
        r"it's all in the latest",
        r"(?:is|are) pleased to announce",
        r"please join me in",
        r"welcome to corvane",
        r"new hire",
        r"media inquiries",
        r"quote of the day",
        r"columnist",
        r"writes to the",
        r"hopes that you and your (?:family|families)",
        r"(?:flood|storm|hurricane|disaster) relief",
        r"tropical storm",
        r"community relations",
    ),
    "event": (
        r"webcast",
        r"register (?:now|today)",
        r"please rsvp",
        r"registration information",
        r"you are invited to attend",
        r"limited seating",
        r"bag lunch",
        r"open enrollment",
    ),
    "helpdesk": (
        r"administrator",
        r"it staff",
        r"help desk",
        r"helpdesk",
        r"posting site",
        r"informational posting",
        r"critical notice",
        r"notice to (?:all )?shippers",
        r"we apologize for (?:the|any) inconvenience",
        r"attention!",
        r"the following .{0,40}(?:is ready|has been (?:completed|approved|submitted))",
        r"to the attention of",
    ),
}
AUTOMATED = _categories(AUTOMATED_WORDS)
SPAM_WORDS: dict[str, tuple[str, ...]] = {
    "promotion": (
        r"save \d+%",
        r"free (?:trial|offer|gift)",
        r"act now",
        r"limited time",
        r"\$\d+ off",
        r"special offer",
        r"order now",
        r"coupon",
        r"\d+% off",
        r"holiday collection",
        r"special rates",
        r"subscription offer",
        r"switch now",
        r"cash bonus",
    ),
    "finance_spam": (
        r"life insurance",
        r"mortgage rates",
        r"no extra cost",
        r"winner",
        r"lottery",
        r"past performance does not",
        r"\$\$\$",
    ),
    "web_junk": (
        r"visit our website",
        r"fortune (?:link|article|magazine)",
        r"\[image\]",
        r"free download",
        r"mailing list id",
        r"web site design",
        r"data recovery",
        r"promotional partner",
        r"msn messenger",
        r"advertisement here",
        r"update your account",
        r"sign-in and update",
        r"thank you for subscribing",
    ),
}
SPAM_SHOUTING = re.compile(r"!!!|\bFREE\b|CLICK HERE")  # case-sensitive: "feel free" is fine
SPAM = _categories(SPAM_WORDS)


def spam_reason(text: str) -> str | None:
    return category(SPAM, text) or ("shouting" if SPAM_SHOUTING.search(text) else None)


# a job applicant writing (no persona of the roster is one)
RECRUIT = re.compile(
    r"\bmba\b|\bcandidates?\b|class of (?:19|20)\d\d|\bresume\b|\binternship|telephone interview|"
    r"interview with you|interviewers|accept your offer|offer of employment|your interview|"
    r"\binterview(?:s|ed|ing)?\b|\binterview packet|"
    r"\brotation in your group|super saturday|\brecruiting\b|\bjob offer\b|\bmy interview",
    re.IGNORECASE,
)
# content that does not belong in an executive's work mailbox from a colleague
OFF_ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "career": (r"your boss\b", r"my boss\b", r"get promoted\b", r"i'?m a (?:girl|woman|guy|man)\b"),
    "home": (
        r"your (?:new )?home\b",
        r"the house\b",
        r"my (?:\w+ )?house\b",
        r"earnest money",
        r"subdivision",
        r"homeowner",
        r"garden\b",
        r"garage\b",
        r"bathroom\b",
        r"repaint\w*",
        r"remodel\w*",
        r"our house\b",
    ),
    "family": (
        r"dad\b",
        r"mom\b",
        r"mommy\b",
        r"daddy\b",
        r"grandma\b",
        r"grandpa\b",
        r"honey\b",
        r"sweetie\b",
        r"sweetheart\b",
        r"xoxo\b",
        r"love (?:you|ya)\b",
        r"wedding\b",
        r"baby\b",
        r"birthday\b",
        r"church\b",
        r"cousin\b",
        r"daughter\b",
        r"nephew\b",
        r"niece\b",
    ),
    "school": (
        r"the school\b",
        r"university of\b",
        r"textbooks?\b",
        r"homework\b",
        r"syllabus\b",
        r"semester\b",
        r"professor\b",
        r"classroom\b",
        r"students?\b",
        r"tuition\b",
        r"exams?\b",
    ),
    "media_sports": (
        r"on our show\b",
        r"tv\b",
        r"football\b",
        r"coach\b",
        r"nickname\b",
        r"mp3\b",
        r"music\b",
        r"golf\b",
        r"holes\b",
        r"tee times?",
    ),
    "social": (
        r"(?:friday|saturday|sunday) night",
        r"this weekend\b",
        r"babysit\w*",
        r"at my house",
        r"get together\b",
        r"beer\b",
        r"burgers?\b",
    ),
    "personal_money": (r"personal investment", r"running out of cash"),
}
OFF_ROLE = _categories(OFF_ROLE_WORDS)
_PRC = re.compile(r"\bPRC\b")  # the performance review committee (case-sensitive)
_LOVE_SIGNOFF = re.compile(r"(?im)^\s*love,")
_NAME_GREETING = re.compile(
    rf"^(?:(?i:hi|hello|dear|hey)\s+)?(?:{_PH}|[A-Z][a-z]+)(?:\s*(?:,|and|&)\s*(?:{_PH}|[A-Z][a-z]+))*"
    r"\s*[,:]?\s*$"
)
_REAL_FIRST = (N.COMMON_FIRST | N.AMBIGUOUS_FIRST | N.PLACES) - set(N.FAKE_FIRST) - N.PERSONA_FIRST
# public holidays: their dates cannot follow the shifted calendar
HOLIDAY = re.compile(
    r"christmas|x-?mas|thanksgiving|new year|easter|halloween|memorial day|labor day|independence day|"
    r"fourth of july|4th of july|valentine|hanukkah|president'?s day|\bking day\b|"
    r"\bmlk\b|\bholidays?\b|merry|good friday|passover|mother'?s day|father'?s day",
    re.IGNORECASE,
)
# three or more lines holding nothing but a person: a list of names (or of markets that the
# pseudonymizer took for names)
NAME_LIST = re.compile(rf"(?m)(?:^[ \t]*-?[ \t]*{_PH}(?:[ \t]+{_PH})?[ \t]*[,;]?[ \t]*\n){{3,}}")
# a first-name placeholder alone on a line inside a body (a stray list item, not a sign-off)
STRAY_NAME = re.compile(r"(?m)^\S.*\n[ \t]*\{\{[pq]:\d+:f\}\}[ \t]*\n(?=.*\S)")
LIST_NAME = re.compile(r"(?m)[-:]\s+([A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?\s*$")
HTML = re.compile(r"<\s*/?\s*(?:html|body|table|tr|td|div|font|img|br|p|span|head)\b", re.I)
SEPARATOR = re.compile(r"(?m)^\s*[_\-=*~]{12,}\s*$")
DISCLAIMER = re.compile(
    r"intended recipient|confidentiality (?:footer|notice)|privileged and confidential|"
    r"received this (?:e-?mail|message) in error|internet email",
    re.IGNORECASE,
)
INTRODUCTION = re.compile(
    r"\bmy name is\b|\bthis is \{\{|recently (?:come on board|joined)|prior to joining", re.I
)
NEWSLETTER = re.compile(
    r"newsletter|edition|\bissue\b|subscri\w+|publication|digest|bulletin|briefing|weekly|"
    r"\bdaily\b|\breport\b|webcast|conference|registration|headlines|market (?:update|watch)",
    re.IGNORECASE,
)
FIRST_PERSON = re.compile(r"\bI\b|\bI'm\b|\bI've\b|\bmy\b|\bme\b")
GOVERNANCE = re.compile(
    r"\bboard (?:meeting|members?|of directors|approval|package|materials|deck|session|vote)|"
    r"\bthe board\b|committee|director|governance|shareholder|meeting|strateg|\bceo\b|chairman|audit|"
    r"council|compensation|\bplan\b|company|management|acquisition|merger|budget|investor|"
    r"agenda|results|earnings|policy|risk|capital|financ",
    re.IGNORECASE,
)
DAMAGE = re.compile(
    r"(?m)\b[a-z]{3,}I\b|\[\(555\)|\bI%ve\b|^\s*-?\s*(?!FYI|OK|TBD|ASAP|NO|YES|PS)[A-Z]{1,3}\s*$(?=\n\s*\n\s*[A-Za-z][^\n]{12,})|"
    r"^(\{\{[pq]:\d+:f\}\}|[A-Z][a-z]+):\s*\n(?:\s*\n)*\s*\1:\s*$|^\s*home\b.{0,10}\(?555|"
    r"^\s*;\s*$|^Importance:|(?<![A-Z])&[A-Z][a-z]|^(?:\d\d:){2}\d\d |\bwrote:\s*$|\((?i:see attached file):|(?:Ms|Mrs|Mr)\.?\s+\{\{[ps]:\d+:l\}\},|\?(?=https?:)|\b[A-Za-z]+\?[a-z]{2,}|\\$|\?\s*(?:19|20)\d\d\b|\?{2,}"
)
URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
NEWSWIRE_EXTRA = re.compile(r"news brief|press release|\(AP\)|\bUPI\b|newswires?", re.IGNORECASE)
_FUNCTION_END = re.compile(
    r"\b(?:a|an|the|of|to|for|with|and|or|will|not|that|which|in|on|at|by|is|be|are|was|"
    r"would|should|could|from|as|this|these|our|your|their|its|been|has|have|had|were|may|"
    r"can|do|does|you|we|they|he|she|it|them|him|her|us|if|when|but|so|than|then|into|about|"
    r"some|any|all|each|very|more|also|please)$",
    re.IGNORECASE,
)


def words(text: str) -> int:
    """Words of two or more letters, ignoring URLs and placeholders."""
    bare = URL.sub(" ", re.sub(_PH, " ", text))
    return len(re.findall(r"[A-Za-z]{2,}", bare))


def truncated(body: str) -> bool:
    """The body stops mid-sentence (a chain split or a size cut): its last content line is a
    full-width wrapped line without end punctuation, or ends on a function word."""
    lines = [ln.strip() for ln in strip_signature(body)[0].split("\n") if ln.strip()]
    if lines and lines[-1].endswith(":"):
        return True  # "Home phone numbers:" with nothing after it
    lines = [ln for ln in lines if not re.fullmatch(rf"(?:{_PH}\W*)+", ln)]
    if not lines:
        return False
    last = lines[-1]
    if last.endswith(":"):
        return True  # "Home phone numbers:" with nothing after it
    if re.search(r"[.!?;)\"'>\]]$|^\W*$", last):
        return False
    return len(last) >= 60 or (len(last.split()) >= 2 and _FUNCTION_END.search(last) is not None)


def junk_reason(segments: list[dict[str, object]]) -> str | None:
    """Why a record is not usable mail (None if it is)."""
    for i, seg in enumerate(segments):
        body = str(seg.get("body") or "")
        subject = str(seg.get("subject") or "")
        if re.search(
            r"\.(?:url|pdf|docx?|xlsx?|pptx?)\b|https?://|www\.|@", subject, re.IGNORECASE
        ):
            return "url-subject"
        content = STUB_LINE.sub("", strip_signature(body)[0])
        if body and words(content) < (5 if i == 0 else 3):
            return "near-empty"
        if body and DAMAGE.search(body):
            return "damaged"
        if body and truncated(body):
            return "truncated"
    text = "\n".join(
        str(s.get("body") or "") + "\n" + str(s.get("subject") or "") for s in segments
    )
    joined = "\n".join(str(s.get("body") or "").rstrip() for s in segments)
    starts = re.findall(r"(?:^|[.!?]\s+)([A-Za-z])", re.sub(_PH, "Name", joined))
    if sum(c.islower() for c in starts) >= 3 and sum(c.islower() for c in starts) * 2 > len(starts):
        return "lowercase"  # casual all-lowercase text hides names from the detectors
    if SEPARATOR.search(joined):
        return "quoted-block"
    for seg in segments:  # a literal first name left in a greeting ("Thea and Rose,", "Dutch,")
        first = next(
            (ln.strip() for ln in str(seg.get("body") or "").split("\n") if ln.strip()), ""
        )
        m = _NAME_GREETING.match(first)
        if m and any(
            w in _REAL_FIRST for w in re.findall(r"\b[A-Z][a-z]+\b", re.sub(_PH, "", first))
        ):
            return "residual-name"
    letters = re.findall(r"[A-Za-z]", re.sub(_PH, "", joined))
    if len(letters) >= 40 and sum(c.isupper() for c in letters) > 0.6 * len(letters):
        return "shouting"
    first_line = next((ln.strip() for ln in str(segments[-1].get("body") or "").split("\n")
                       if ln.strip()), "")  # fmt: skip
    if re.match(r"(?:is|are|was|were|and|but|or|of|to|which|that|than)\b", first_line):
        return "starts-mid-sentence"
    for seg in segments:
        content = strip_signature(str(seg.get("body") or ""))[0]
        if len(segments) == 1 and re.search(r"(?i)\bbelow\b", content) and words(content) < 12:
            return "dangling-reference"  # "Please see revised schedule below" with nothing below
        if re.search(r"(?i)here'?s my (?:e-?mail address|cell|number)\W*$", content):
            return "dangling-reference"
    if NAME_LIST.search(joined) or STRAY_NAME.search(joined):
        return "name-list"
    for m in LIST_NAME.finditer(joined):  # "30 Minutes - Grant": a first name left as it was
        if m.group(1) in N.COMMON_FIRST or m.group(1) in N.AMBIGUOUS_FIRST:
            return "residual-name"
    if spam_reason(text):
        return "spam"
    for name, rx in (("disclaimer", DISCLAIMER), ("newswire", NEWSWIRE_EXTRA), ("html", HTML),
                     ("holiday", HOLIDAY)):  # fmt: skip
        if rx.search(text):
            return name
    return None


LAWYER = re.compile(r"\bmy client\b|\bdeposition|\bplaintiff|\bprotective order|\bCLE\b", re.I)
# an outsider (bank, auditor, IT vendor, partner) speaking as a company insider
INSIDER_VOICE = re.compile(
    r"\b(?:our|we|us)\b[^.!?\n]{0,60}?\b(?:desks?|traders?|deal entry|deal tickets?|deals?|"
    r"positions?|book|ops|operations|scheduling|schedulers?|nominat\w*|storage balance|"
    r"confirm\w*|p&l|servers?|department|employees|staff|floor|office|mlp|partnership|counsel|"
    r"lawyers|attorneys|auditors?|board|ceo|cfo|systems?|site|plant|permits?|development|budget|"
    r"hiring|headcount|accounting|treasury|mid-?marketing|agency relationship|ops group|duties|"
    r"responsibilities|new product|final filing|filings?|expenses)\b"
    r"|\bcorvane (?:user community|employees|staff|users|people)\b|\bcorvane'?s? (?:treasury|"
    r"accounting|credit|legal|it) department\b|\bthe corvane [\w .]{0,30}department\b|"
    r"\b(?:in|from|with) (?:credit|accounting|legal|operations|scheduling|risk|trading)\b|"
    r"\b(?:do|did|can|should) we (?:margin|trade|book|confirm|schedule|nominate|invoice|hedge)\b|"
    r"\b(?:doing|running|covering) (?:cash|the desk|scheduling|nominations|the book)\b|"
    r"\bcorvanetrade\b|\bctr\b|\btidewater\b|\bgridbook\b|\btrax\b|\binternally,|"
    r"work order|\bwbs\b|cost center|for coding|counterparty to trade|\bwe \([^)]{1,40}\) have\b|"
    r"\binternally\b|acquire the software|the traders\b|counterparties transacting|"
    r"trade count|travel budget|outside services budget|reimburse us\b|our (?:initiatives|causes)\b|"
    r"represent (?:cns|cna|ces|corvane)\b|the only paper we have|negotiate a gisb|\bto corp\b",
    re.IGNORECASE,
)
# what each outside persona writes about (`role_reason(..., sender_id=...)`)
PERSONA_DOMAIN: dict[str, re.Pattern[str]] = {
    "auditor": re.compile(
        r"audit|accounting|controls?\b|financial statements?|fieldwork|"
        r"walk-?through|sox\b|reconcil|journal entr|disclos|reserves?\b|"
        r"valuation|10-?[kq]\b|quarter-?end|year-?end|filing|gaap|testing|"
        r"workpapers?|engagement|close\b|budget|forecast|invoice|"
        r"review of (?:the )?(?:controls|financial|accounts)|revenue|earnings",
        re.I,
    ),
    "it_vendor": re.compile(
        r"system|software|licen[cs]e|server|upgrade|support|application|"
        r"network|install|migrat|hardware|laptop|desktop|outlook|e-?mail "
        r"account|helpdesk|renewal|maintenance|version|release|database|"
        r"backup|outage|patch",
        re.I,
    ),
    "banker": re.compile(
        r"bank|credit|loan|facility|letter of credit|\blc\b|wire|treasury|"
        r"rates?\b|deposit|account|collateral|isda|guarant|financing|covenant|"
        r"swap|hedge|cash|threshold|exposure|option|premium|price|worksheet|"
        r"payment|invoice|budget|forecast|fund",
        re.I,
    ),
    "outside_counsel": re.compile(
        r"agreement|contract|counsel|legal|court|filing|ferc|regulat|"
        r"clause|draft|isda|resolution|signature|litigation|claim|"
        r"opinion|lawyer|attorney|transaction",
        re.I,
    ),
    "bd_partner": re.compile(
        r"deal|transaction|opportunit|partner|meeting|proposal|acquisition|"
        r"financing|investor|introduc|counterpart|market|price|trade|"
        r"agreement|contract|terms|version",
        re.I,
    ),
}
# a colleague who does not know the recipient ("I hope I'm speaking to the right person")
STRANGER = re.compile(
    r"right person|correct person|appropriate person|not sure who (?:to|i should)|"
    r"whom (?:to|i should) contact|i found your name|your name (?:was|has been) (?:given|passed)|"
    r"was referred to you|i was told (?:that )?you|introduce myself|we have not met|"
    r"haven't (?:yet )?met|\bi work in\b|other lawyers|(?:wasn't|was not|am not|'m not) sure who|"
    r"look forward to (?:working|speaking) with you|nice to meet you|pleasure (?:to meet|meeting) you|"
    r"your company\b|your firm\b|your organi[sz]ation\b|corvane (?:has|have) refused|"
    r"problems they have with us",
    re.IGNORECASE,
)
# back-office trading operations (deal entry, matching, confirmations, scheduling, flows)
BACK_OFFICE = re.compile(
    r"\bdeal\s*#|\bdeals?\s+\d|#\s?\d{5,}|match the following|deal entry|deal tickets?|"
    r"(?:killed|entered|input|changed) (?:the |this |a |these |two )?deals?|bridg(?:e|ing)\b|"
    r"delivery point|nominat\w*|\bnoms?\b|scheduler|(?:be|been|to be|not to be) confirmed|"
    r"\bconfirm(?:ed|ation)?s? (?:for|of) (?:the |this )?deals?|contract number|\bHE \d|"
    r"hour ending|\bk#|cut (?:wrong|from)|checkout|flows? to\b|\bcuts\b|deal (?:is|was) in|"
    r"\bHE ?\d|prescheduled|off-peak hours|unit id|confirm the trade|(?m:^\s*\d{5,}\s*-)|"
    r"broker (?:mistake|fee)|for (?:their )?execution and return|print (?:off )?(?:two|as many)|"
    r"have \w+ (?:sign|initial)|usual distribution|for documentation|storage (?:proxy )?schedules|"
    r"nominations|\bpipeline schedul|deals? (?:is |was |are )?(?:entered|input|confirmed|killed)|"
    r"deal confirmed|the confirmation\b|counterparty contact|(?m:^\s*\d{5,}\s*$)|"
    r"settle (?:with|the)\b|sign and approve|scada|your folder|billing system|entered against|"
    r"chang\w+ (?:any )?deals?\b|minibook|\bmtm\b|\bentr(?:y|ies) for\b",
    re.IGNORECASE,
)
# agreement negotiation (not the job of the executive assistant or a board member)
NEGOTIATION = re.compile(
    r"revised (?:\w+ )?agreement|changes we discussed|redline|red-?lined|mark-?up|"
    r"draft (?:agreement|contract)|term sheet|can you live with|(?:tsa|isda|psa|ppa) revisions?|"
    r"counter-?propos\w*|negotiat\w*|slides we're preparing|precedent agreement|"
    r"paragraph is now|our (?:comments|position) on the (?:agreement|contract)|"
    r"sale and purchase (?:contract|agreement)|shopping (?:it )?around|purchase agreement",
    re.IGNORECASE,
)
# the chief executive thanking a subordinate for an opportunity ("It was an honor to represent")
DEFERENCE = re.compile(
    r"honou?r to (?:represent|be|serve)|appreciate the (?:platform|opportunity) you|"
    r"thank you (?:again )?for having me|for the opportunity to (?:present|speak|join)",
    re.IGNORECASE,
)
# clerical work nobody asks the chief executive to do
CLERICAL = re.compile(r"(?i)please print|print in colou?r|make (?:\d+ )?copies|fax (?:it|this|them)|"
                      r"book (?:a|the) (?:room|flight|car)")  # fmt: skip
# company-secretarial paperwork: prepared, certified and circulated by counsel or the company
# secretary, never by the chief executive or another business executive
CORPORATE_SECRETARIAL = re.compile(
    r"\bincumbency\b|\bsecretary'?s certificate|\bofficers?'? certificates?|good standing|"
    r"specimen signatures?|\bminute books?\b|articles of (?:incorporation|organi[sz]ation)|"
    r"banking resolutions?|unanimous written consent|corporate seal",
    re.IGNORECASE,
)
SECRETARIAL_OFF_LIMITS = ("ceo", "cfo", "cbo", "coo")
# trading and treasury work that the head of people does not review
TRADING_FINANCE = re.compile(r"(?i)letter of credit|\blc\b|\bisda\b|\bswaps?\b|\bhedge|"
                             r"collateral|counterpart(?:y|ies)|\bmtm\b|curtailment")  # fmt: skip
INSIDE_GROUPS = ("exec", "director", "assistant")
EXECUTIVES = ("ceo", "cfo", "cbo", "coo", "general_counsel")
OUTSIDE_GROUPS = ("vendor", "external", "newsletter", "unknown")


def role_reason(group: str, text: str, group_hint: str = "", *, sender_id: str = "",
                to_groups: tuple[str, ...] = (), parties: tuple[str, ...] = (),
                to_ids: tuple[str, ...] = ()) -> str | None:  # fmt: skip
    """Why `text` cannot plausibly come from a persona of `group` (None if it can).
    `group_hint` is "personal" for the personal contact and "lawyer" for counsel; `sender_id`
    the persona id, `to_groups` / `to_ids` the groups and ids of the To recipients and `parties`
    the persona ids of sender and recipients (To and Cc)."""
    if RECRUIT.search(text):
        return "applicant"
    if LAWYER.search(text) and group_hint != "lawyer":
        return "lawyer-voice"
    if group == "newsletter":
        markers = {m.lower() for m in NEWSLETTER.findall(text)}
        if (len(markers) < 2 or FIRST_PERSON.search(text) or "corvane" in text.lower()
                or words(URL.sub(" ", text)) < 40):  # fmt: skip
            return "not-a-newsletter"  # an outside newsletter, not an internal notice
        return None
    if AUTOMATED.search(text):
        return "mass-mail"
    if group != "external" and (OFF_ROLE.search(text) or _PRC.search(text)
                                or _LOVE_SIGNOFF.search(text)):  # fmt: skip
        return "off-role"
    if group_hint == "personal" and FRIEND_BUSINESS.search(text):
        return "business-from-a-friend"
    if group in ("exec", "director", "assistant") and INTRODUCTION.search(text):
        return "introduction"
    if group == "board" and not GOVERNANCE.search(text):
        return "off-topic-for-board"
    return _voice_reason(group, text, group_hint, sender_id, to_groups, parties, to_ids)


def _voice_reason(group: str, text: str, group_hint: str, sender_id: str,
                  to_groups: tuple[str, ...], parties: tuple[str, ...],
                  to_ids: tuple[str, ...]) -> str | None:  # fmt: skip
    """Voice and recipient coherence: who says this, to whom."""
    for rid in to_ids:  # an outside recipient is written to about its own field
        domain = PERSONA_DOMAIN.get(rid)
        if domain is not None and not domain.search(text):
            return "off-domain-recipient"
    if group in ("vendor", "external") and group_hint != "personal":
        if INSIDER_VOICE.search(text) or BACK_OFFICE.search(text):
            return "insider-voice"  # incl. the company's own deal entry and confirmations
        domain = PERSONA_DOMAIN.get(sender_id)
        if domain is not None and not domain.search(text):
            return "out-of-domain"
        if to_groups and all(g in OUTSIDE_GROUPS for g in to_groups):
            return "outsiders-only"
    if group in (*INSIDE_GROUPS, "board") and STRANGER.search(text):
        return "stranger-voice"
    if "ceo" in parties and BACK_OFFICE.search(text):
        return "back-office-for-ceo"
    if (group in INSIDE_GROUPS and to_groups and all(g in OUTSIDE_GROUPS for g in to_groups)
            and BACK_OFFICE.search(text)):  # fmt: skip
        return "back-office-to-outsider"
    if group in ("assistant", "board") and BACK_OFFICE.search(text):
        return "operations-from-assistant-or-board"
    if sender_id == "ceo" and DEFERENCE.search(text):
        return "deference-from-ceo"
    if any(t in EXECUTIVES for t in to_ids) and CLERICAL.search(text):
        return "clerical-request-to-executive"
    if sender_id in SECRETARIAL_OFF_LIMITS and CORPORATE_SECRETARIAL.search(text):
        return "secretarial-work-by-executive"
    if ("director_hr" in to_ids or sender_id == "director_hr") and TRADING_FINANCE.search(text):
        return "finance-for-hr"
    own_title = {"board_chair": r"\bthe chairman\b", "ceo": r"\bthe ceo\b",
                 "cfo": r"\bthe cfo\b", "general_counsel": r"\bthe general counsel\b"}  # fmt: skip
    if sender_id in own_title and re.search(own_title[sender_id], text, re.IGNORECASE):
        return "own-role-in-third-person"
    if (
        group in ("assistant", "board") or any(g in ("assistant", "board") for g in to_groups)
    ) and (NEGOTIATION.search(text)):
        return "negotiation-by-assistant-or-board"
    return None


FRIEND_BUSINESS = re.compile(
    r"\b(?:deal|contract|invoice|worksheet|agreement|trading|pipeline|counterparty|ferc|team|"
    r"schedul\w*|attached|vacation|update|call|meeting|markets?|corvane|work here|floor|"
    r"forecast|budget|nominations?|interconnect|meter|tariff|credit|desk|confirm\w*)\b",
    re.IGNORECASE,
)


def self_reference(template: str, refs: list[str]) -> bool:
    """The sender's own name appears in the body (outside the greeting and sign-off)."""
    return any(re.search(r"\{\{" + re.escape(r) + r":[nfl]\}\}", template) for r in refs)


# ----------------------------------------------------------------------- the frame

_GREET_WORD = r"(?i:hi|hello|dear|hey|good morning|good afternoon|good evening|morning|greetings)"
_NAME_ITEM = rf"(?:{_PH}(?:\s+{_PH})?)"
_GREETING = re.compile(
    rf"^(?P<word>{_GREET_WORD}\b[ ,]*)?(?P<title>(?i:mr|mrs|ms|dr|miss)\.?\s+)?"
    rf"(?P<names>{_NAME_ITEM}(?:\s*(?:,|and|&|/)\s*{_NAME_ITEM})*|(?<=[ .])[A-Z][a-z]+)"
    r"\s*(?P<punct>[,:!.;]+|\s-+|--)?\s*$"
)
_INLINE_GREETING = re.compile(
    rf"^(?P<word>{_GREET_WORD}\s+)?(?P<name>{_PH})(?P<punct>\s*[,:.!]|\s+-+)\s+(?=\S)"
)
_CLOSING = re.compile(
    r"^(?P<word>(?i:thanks(?: again| so much| in advance| a lot)?|thank you(?: very much)?|"
    r"many thanks|regards|best regards|kind regards|warm regards|warmest regards|best wishes|"
    r"best|cheers|sincerely|rgds|thx|thanx|respectfully|talk soon|later|take care|all the best|"
    r"love|love ya|thank|very truly yours|yours truly|yours sincerely|sincerely yours|yours))"
    rf"[,.!;]*(?:\s*-*\s*(?:{_PH}(?:[ .]+{_PH})*|[A-Za-z]+)\.?)?\s*$"
)
_NAME_LINE = re.compile(
    rf"^-*\s*(?:{_PH}[ .,]*)+(?:[A-Z]\.?)?-*\s*$|^-*\s*[A-Za-z]{{1,3}}\.?$|^-*\s*[a-z]{{2,12}}\.?$"
)
_STRONG_SIG = re.compile(
    r"555-01\d\d|\(555\)|\bx55\d\d\b|@|\{\{[pqsx]:\d+:e\}\}|https?://|www\.|"
    + re.escape(N.FAKE_STREET)
    + r"|P\.O\. Box|\b"
    + N.FAKE_ZIP
    + r"\b|"
    r"\b(?:phone|fax|cell|mobile|pager|direct|office|tel|ph|voice|e-?mail|web)\b\s*[:#.(]|"
    r"\((?:ph|fax|phone|cell|office|help)\)|\bCT \d|^[-_*=~]{3,}$|^sent from|"
    r"\b(?:vice president|vp|director|manager|counsel|analyst|specialist|coordinator|assistant|"
    r"president|ceo|cfo|coo|chair|chairman|partner|associate|candidate|executive|administrator|"
    r"officer|head of|senior|sr\.|trader|engineer|consultant|attorney|paralegal|department|"
    r"dept\.?|desk|team|group)\b|\b(?:inc|llc|corp|l\.p\.|ltd|company)\b\.?|^corvane\b|^\".*\"$",
    re.IGNORECASE,
)
FYI = re.compile(r"(?i)^(?:fyi|f\.y\.i\.|see below|please see below)[.!,]*$")
_SALUTE = re.compile(r"(?i)^(?:all|team|everyone|folks|guys|gentlemen|ladies|hi all|hello all|"
                     r"fyi|thanks|please|attached|note|update|reminder)\b")  # fmt: skip


def _weak_sig(line: str) -> bool:
    """A short signature-ish line: a name, a title, an organisation, a place."""
    if re.fullmatch(r"<[^<>]*>", line):
        return True  # "<Microsoft Clip Gallery>"
    return len(line.split()) <= 7 and not re.search(r"[.!?]$", line) and not FYI.match(line)


def _signature_block(after: list[str]) -> bool:
    """Lines after a sign-off that form a signature block: short or contact-like lines, with at
    most one other line (a motto, a disclaimer line) when the block has contact details."""
    if len(after) > 12:
        return False
    strong = sum(1 for a in after if _STRONG_SIG.search(a))
    other = [a for a in after if not (_STRONG_SIG.search(a) or _weak_sig(a))]
    return not other or (strong >= 2 and len(other) == 1 and len(other[0].split()) <= 12)


def strip_signature(template: str) -> tuple[str, str | None]:
    """Remove the sign-off and the signature block. Returns (body, closing word or None)."""
    lines = template.rstrip().split("\n")
    n = len(lines)
    floor = max(1, n - 16)
    for i in range(n - 1, floor - 1, -1):
        m = _CLOSING.match(lines[i].strip())
        if not m:
            continue
        after = [ln.strip() for ln in lines[i + 1 :] if ln.strip()]
        if _signature_block(after):
            word = m.group("word")
            return "\n".join(lines[:i]).rstrip(), word[:1].upper() + word[1:].lower()
        break
    for i in range(floor, n):
        line = lines[i].strip()
        if not line or not _NAME_LINE.match(line) or FYI.match(line) or _SALUTE.match(line):
            continue
        after = [ln.strip() for ln in lines[i + 1 :] if ln.strip()]
        if _signature_block(after):
            return "\n".join(lines[:i]).rstrip(), None
    while len(lines) > 1 and (
        not lines[-1].strip()
        or _STRONG_SIG.search(lines[-1].strip())
        and len(lines[-1].split()) <= 10
    ):
        lines.pop()
    body = "\n".join(lines).rstrip()
    # "... the week. DF", "... be helpful. Tx {{q:1:f}}": an inline sign-off at the very end
    body = re.sub(rf"(?<=[.!?])\s+(?:(?i:tx|thx|thanks|regards|cheers)[,.!]?\s+{_PH}\.?|[A-Z]{{2,3}}\.?)$",
                  "", body)  # fmt: skip
    return body, None


_GROUP_GREETING = re.compile(
    r"^(?P<word>(?i:hi|hello|hey|dear|good morning|good afternoon|morning)[ ,]+)?"
    r"(?P<group>(?i:all|team|everyone|everybody|folks|guys|gang|y'all|ladies and gentlemen))"
    r"(?P<punct>\s*[,:!.-]+|\s+-+)?(?P<rest>\s+\S.*)?$"
)


def rewrite_greeting(template: str, to_first: str | None, *, single: bool = True) -> str:
    """The opening line addresses the bound recipient ("Dear Mr. X," -> "Dear Helena,"). With
    one recipient (`single`), a greeting to several people or to a group ("Hi all,", "Guys,")
    names that recipient; otherwise it becomes "Hi all,"."""
    lines = template.split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if idx is None:
        return template
    line = lines[idx].strip()
    m = _GREETING.match(line)
    if m and (m.group("word") or m.group("title") or "{{" in m.group("names")):
        names = re.findall(_NAME_ITEM, m.group("names"))
        punct = (m.group("punct") or "").strip()
        punct = "," if not punct or punct.startswith("-") else punct
        word = (m.group("word") or "").strip(" ,")
        if to_first is None or (len(names) > 1 and not single):
            lines[idx] = f"{word or 'Hi'} all{punct if punct != '-' else ','}"
        else:
            lines[idx] = f"{word + ' ' if word else ''}{to_first}{punct}"
        return "\n".join(lines)
    nick = re.match(
        rf"^(?:{_PH}|[A-Z][a-z]+)\s+(?i:lady|man|dude|guy|girl|buddy|boss|friend)"
        r"\s*(?P<punct>[,:!])\s*$",
        line,
    )  # "X lady," -> the recipient
    if nick and to_first:
        lines[idx] = f"{to_first}{nick.group('punct')}"
        return "\n".join(lines)
    listed = re.match(
        rf"^(?:{_PH}|[A-Z][a-z]+)(?:\s*(?:,|and|&)\s*(?:{_PH}|[A-Z][a-z]+))+"
        r"\s*(?P<punct>[,:])\s*$",
        line,
    )
    if (
        listed
        and to_first
        and (single or "{{" in line)
        and not re.search(r"(?i)ladies|gentlemen|\ball\b|team|everyone", line)
    ):
        lines[idx] = f"{to_first}{listed.group('punct')}"
        return "\n".join(lines)
    group = _GROUP_GREETING.match(line)
    if group and single and to_first and group.group("group").lower() != "ladies and gentlemen":
        word = (group.group("word") or "").strip(" ,")
        punct = (group.group("punct") or ",").strip() or ","
        punct = "," if punct.startswith("-") else punct
        rest = group.group("rest") or ""
        lines[idx] = f"{word + ' ' if word else ''}{to_first}{punct}{rest}"
        return "\n".join(lines)
    if to_first and re.fullmatch(r"[A-Z]{2,3}[,:]", line):  # "JW,"
        lines[idx] = to_first + line[-1]
        return "\n".join(lines)
    inline = _INLINE_GREETING.match(line)
    if inline and to_first:
        word = inline.group("word") or ""
        lines[idx] = word + to_first + inline.group("punct") + " " + line[inline.end() :]
    return "\n".join(lines)


def reframe(template: str, *, to_first: str | None, sender_first: str | None,
            single: bool = True) -> str:  # fmt: skip
    """Greeting to the bound recipient, the original sign-off and signature block removed and
    the sender's own sign-off appended (no sign-off for `sender_first=None`)."""
    body, closing = strip_signature(template)
    body = rewrite_greeting(body, to_first, single=single)
    if sender_first is None:
        return body
    if closing:
        return f"{body}\n\n{closing},\n{sender_first}"
    return f"{body}\n\n{sender_first}"


# ------------------------------------------------------------------------ subjects

_LEAD = re.compile(
    r"^(?:(?:hi|hello|dear|hey)\b[^,:\-]*(?:[,:]|\s--?)\s*|fyi[.:,-]*\s*|"
    r"(?:attached|enclosed)(?: below| here(?:to|with))?,? (?:is|are|please find|you will find)\s+|"
    r"please (?:find|see) (?:the )?attached\s+|here (?:is|are)\s+|"
    r"(?:just )?a (?:quick|brief) (?:note|reminder)(?: on| about| that)?\s+|"
    r"(?:as|per) (?:we|our|my|your) [a-z ]+?,\s*|i (?:wanted|want) to (?:let you know|"
    r"remind you)(?: that)?\s+|please note(?: that)?\s+)+",
    re.IGNORECASE,
)
_STOP_TAIL = re.compile(r"(?:\s+(?:i'd|i'll|i'm|we'd|we'll|of|the|to|and|a|an|for|with|is|are|that|this|on|in|at|by|"
                        r"from|as|be|has|have|was|we|i|you|it|our|your|will|w/|&|my|their|"
                        r"there|been|can|could|would|should|may|if|when|so|but|or|any|"
                        r"some|all))+$", re.IGNORECASE)  # fmt: skip
FALLBACK_SUBJECTS = {
    "board": "Board follow-up", "legal": "Legal question", "finance": "Finance follow-up",
    "deals": "Deal update", "hr": "People update", "vendor": "Vendor follow-up",
    "scheduling": "Scheduling", "ops": "Operations update", "personal": "Catching up",
    "newsletter": "This week's briefing", "general": "Quick question",
}  # fmt: skip


def derive_subject(body: str, topic: str = "general") -> str:
    """A subject for a message that has none: a short title line as it is, otherwise the
    first clause of the first sentence without filler ("Attached is", "Hi X,")."""
    holders: list[str] = []

    def hide(m: re.Match[str]) -> str:  # placeholders become plain words while cutting
        holders.append(m.group(0))
        return f"Zph{len(holders) - 1}z"

    def restore(text: str) -> str:
        return re.sub(r"Zph(\d+)z", lambda m: holders[int(m.group(1))], text)

    body = re.sub(_PH, hide, body)
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        short = [ln for ln in lines if len(ln.split()) <= 7 and not re.search(r"[.!?,]$", ln)]
        paragraphs += lines if len(short) == len(lines) else [" ".join(lines)]
    for raw in paragraphs:
        line = URL.sub("", raw).strip(" ,:;-")
        if "@" in line or "{{x:" in restore(line):
            continue  # an address is never a subject
        if (
            "<<" in line
            or STUB_LINE.search(raw)
            or words(line) < 2
            or _CLOSING.match(restore(line))
        ):
            continue
        if _GREETING.match(restore(raw).strip()):
            continue
        line = re.sub(r"\(?555\)?[ -]?555-01\d\d|555-01\d\d", "", line)
        if re.fullmatch(rf"{_GREET_WORD}[ ,!.]*\w*[,!.]*", line):
            continue  # "Good morning,"
        if len(line.split()) <= 7 and not re.search(r"[.!?]$", line) and line[:1].isupper():
            return restore(line.rstrip(" ."))
        text = _LEAD.sub("", line).lstrip(" -:,.")
        sentence = re.split(r"(?<=[.!?])\s", text)[0]
        clauses = re.split(r",(?!\d)|[;:]|\s-\s|\s(?:because|so that|but)\s", sentence)
        clause = next((c for c in clauses if words(c) >= 4), max(clauses, key=words))
        clause = _STOP_TAIL.sub("", " ".join(clause.split()[:7])).strip(" .!?")
        if clause.count("(") != clause.count(")"):
            clause = clause.replace("(", "").replace(")", "").strip()
        if words(clause) < 2:
            continue
        return restore(clause[:1].upper() + clause[1:])
    return FALLBACK_SUBJECTS.get(topic, "Quick question")


# ------------------------------------------------------------- rendered-text checks

_PSEUDO = "(?:" + "|".join(sorted({*N.FAKE_FIRST, *N.FAKE_LAST}, key=len, reverse=True)) + ")"
# a person pseudonym where a place, desk or asset belongs (the import took a place for a name)
MISPLACED = re.compile(
    rf"\b(?:Short|Long) Term {_PSEUDO}\b|\b{_PSEUDO} (?:Power Pool|Pool|Plant|Station|Hub|Trader|"
    rf"Desk|Pipeline|Field|Unit \d)\b|\b(?:at|from|to) the {_PSEUDO}\b(?! [A-Z])|"
    rf"\bfrom {_PSEUDO} to (?:NP|SP|ZP|COB|Mid)|\b(?:visit(?:ed)?|trip|went|accompanied us) to "
    rf"{_PSEUDO}\b|\b(?:Pt\.|Port|Fort|Lake|Mount|Mt\.) {_PSEUDO}\b|\b{_PSEUDO} campus\b|"
    rf"\b{_PSEUDO} my (?:ignorance|french|delay)\b|\b[A-Z]{{2,4}}\d\w* or {_PSEUDO}\b|"
    rf"\b(?:Black|White|Red|Blue|Green|Grand|Big|Little|Great|Old|New) {_PSEUDO}\b|(?:Street|Avenue|Road|Drive|Blvd|Suite \d+)\.?,"
    rf"\s+{_PSEUDO}(?: {_PSEUDO})?,\s+[A-Z]{{2}}\b"
)
_VOCATIVE = re.compile(
    rf"^\s*(?:(?i:great job|congratulations|well done|thanks|thank you),?\s+)?({_PSEUDO})"
    r"(?:\s*[,:.!\-]|\s+(?:don't|do|did|can|could|would|please|pls|i|we)\b)"
)


def misplaced_names(text: str) -> list[str]:
    """Person pseudonyms in place, desk or asset positions ("Long Term Caleb", "Silas Plant")."""
    return [m.group(0) for m in MISPLACED.finditer(text)]


_MID_VOCATIVE = re.compile(rf"(?m)^\s*({_PSEUDO})\s*[,:]\s+\S")


def stray_vocatives(body: str, names: set[str]) -> list[str]:
    """Lines after the opening that address someone who is not on the mail ("Yara, we put ...")."""
    rest = body.split("\n", 1)[1] if "\n" in body else ""
    return [m.group(1) for m in _MID_VOCATIVE.finditer(rest) if m.group(1) not in names]


def addressed_elsewhere(body: str, to_first: str) -> bool:
    """The body opens by addressing someone other than its recipient ("Elena don't think ...")."""
    m = _VOCATIVE.match(body)
    return m is not None and m.group(1) != to_first


def is_greeting_line(line: str) -> bool:
    """A rewritten opening line ("Hi Helena,", "Owen:", "Dear Rohan,") rather than content."""
    return bool(
        re.fullmatch(
            r"\s*(?:(?i:hi|hello|hey|dear|good morning|good afternoon)\s+)?"
            r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*[,:!.-]?\s*",
            line,
        )
    )
