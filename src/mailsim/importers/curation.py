"""Curation rules for corpus-derived leadership mail: what a record must not contain, whether
its content fits the persona it is bound to, and the message frame (greeting, sign-off,
signature block, subject) rewritten for the bound personas.

All functions work on templates (placeholders ``{{p:12:n}}`` still in place) or on rendered
text, never on raw corpus text.
"""

from __future__ import annotations

import re

from mailsim.importers import names as N

_PH = r"\{\{[pqsxo]:\d+:[nfle]\}\}"
PERSON_PH = re.compile(r"\{\{([pqs]:\d+):[nfl]\}\}")

# ------------------------------------------------------------------ content markers

# mass mail and automated notices: only the newsletter persona sends such text
AUTOMATED = re.compile(
    r"automated (?:e-?mail|message|notification)|do not reply|please do not respond|"
    r"the report named|is now available for viewing|\bdear [\w ]{0,40}customers?\b|click here|"
    r"unsubscribe|to be removed|mailing list|you are receiving this|subscribers?\b|"
    r"this e-?mail was sent|\bsurvey\b|market participants|all employees|dear colleagues|"
    r"ladies and gentlemen|to all\b|\ball corvane\b|distribution list|out of the office|"
    r"auto-?reply|this message was sent|to whom it may concern|expense report|request id|"
    r"click on (?:this|the following|the) link|administrator|the following .{0,40}(?:is ready|"
    r"has been (?:completed|approved|submitted))|to the attention of|please rsvp|"
    r"information contained herein|webcast|register (?:now|today)|alert posted|calendar entry|"
    r"critical notice|notice to (?:all )?shippers|we (?:are pleased to |regret to )?announce|"
    r"with (?:deep )?regret|(?:is|are) pleased to announce|please join me in|"
    r"we apologize for the inconvenience|it staff|help desk|posting site|informational posting|"
    r"open enrollment|media inquiries|quote of the day|columnist|attention!|automatically notified|"
    r"registration information|automatic notification|"
    r"urgent notification|click on the url|discussion list|writes to the|helpdesk|your net pay|"
    r"you are invited to attend|limited seating|bag lunch|synchroniz\w+ (?:mailbox|folder)|"
    r"has just sent you|pickup window|e-?card|you don't need to reply|this is to remind you|"
    r"welcome to corvane|new hire",
    re.IGNORECASE,
)
SPAM = re.compile(
    r"save \d+%|life insurance|free (?:trial|offer|gift)|act now|limited time|\$\d+ off|"
    r"no extra cost|winner|lottery|mortgage rates|cash bonus|special offer|order now|"
    r"!!!|\bFREE\b|CLICK HERE|\bvisit our website\b|Fortune (?:link|article|magazine)|"
    r"(?i:coupon|\d+% off|holiday collection|\[image\]|free download|switch now|mailing list id|"
    r"web site design|data recovery|promotional partner|msn messenger|advertisement here|"
    r"special rates|\$\$\$|past performance does not|subscription offer)",
)
# a job applicant writing (no persona of the roster is one)
RECRUIT = re.compile(
    r"\bmba\b|\bcandidates?\b|class of (?:19|20)\d\d|\bresume\b|\binternship|telephone interview|"
    r"interview with you|interviewers|accept your offer|offer of employment|your interview|"
    r"\binterviews\b|\binterviewing\b|"
    r"\brotation in your group|super saturday|\brecruiting\b|\bjob offer\b|\bmy interview",
    re.IGNORECASE,
)
# content that does not belong in an executive's work mailbox from a colleague
OFF_ROLE = re.compile(
    r"\byour boss\b|\bmy boss\b|i'?m a (?:girl|woman|guy|man)\b|\bget promoted\b|\bPRC\b|"
    r"\byour (?:new )?home\b|\bthe house\b|\bmy (?:\w+ )?house\b|earnest money|subdivision|"
    r"homeowner|\bon our show\b|\btv\b|personal investment|running out of cash|the school\b|"
    r"university of\b|nickname|football|\bcoach\b|"
    r"\b(?:dad|mom|mommy|daddy|grandma|grandpa|honey|sweetie|"
    r"sweetheart|xoxo)\b|\blove (?:you|ya)\b|^\s*love,|\b(?:garden|garage|bathroom|repaint\w*|"
    r"remodel\w*|my house|our house|wedding|baby|birthday|church|textbooks?|homework|cousin|"
    r"daughter|nephew|niece|"
    r"syllabus|semester|professor|classroom|students?|tuition|exams?)\b",
    re.IGNORECASE | re.MULTILINE,
)
# public holidays: their dates cannot follow the shifted calendar
HOLIDAY = re.compile(
    r"christmas|thanksgiving|new year|easter|halloween|memorial day|labor day|independence day|"
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
# an attachment stub line (" - report.doc", "<<file.xls>>")
_STUB_LINE = re.compile(
    r"(?im)^[ \t]*(?:-[ \t]+)?(?:<<[^<>]{1,120}>>|[\w.()\-][\w.()\- ]{0,80}\.(?:docx?|xlsx?|pdf|"
    r"pptx?|txt|zip|csv|wpd|rtf|url))[ \t]*$"
)
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
    r"board|committee|director|governance|shareholder|meeting|strateg|\bceo\b|chairman|audit|"
    r"council|compensation|\bplan\b|company|management|acquisition|merger|budget|investor|"
    r"agenda|results|earnings|policy|risk|capital|financ",
    re.IGNORECASE,
)
DAMAGE = re.compile(
    r"(?m)^\s*;\s*$|^Importance:|(?<![A-Z])&[A-Z][a-z]|^(?:\d\d:){2}\d\d |\bwrote:\s*$|\((?i:see attached file):|(?:Ms|Mrs|Mr)\.?\s+\{\{[ps]:\d+:l\}\},|\?(?=https?:)|\b[A-Za-z]+\?[a-z]{2,}|\\$|\?\s*(?:19|20)\d\d\b|\?{2,}"
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
        content = _STUB_LINE.sub("", strip_signature(body)[0])
        if body and words(content) < (4 if i == 0 else 2):
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
    if NAME_LIST.search(joined) or STRAY_NAME.search(joined):
        return "name-list"
    for m in LIST_NAME.finditer(joined):  # "30 Minutes - Grant": a first name left as it was
        if m.group(1) in N.COMMON_FIRST or m.group(1) in N.AMBIGUOUS_FIRST:
            return "residual-name"
    for name, rx in (("spam", SPAM), ("disclaimer", DISCLAIMER), ("newswire", NEWSWIRE_EXTRA),
                     ("html", HTML), ("holiday", HOLIDAY)):  # fmt: skip
        if rx.search(text):
            return name
    return None


LAWYER = re.compile(r"\bmy client\b|\bdeposition|\bplaintiff|\bprotective order", re.I)


def role_reason(group: str, text: str, group_hint: str = "") -> str | None:
    """Why `text` cannot plausibly come from a persona of `group` (None if it can).
    `group_hint` is "personal" for the personal contact and "lawyer" for counsel."""
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
    if group != "external" and OFF_ROLE.search(text):
        return "off-role"
    if group_hint == "personal" and FRIEND_BUSINESS.search(text):
        return "business-from-a-friend"
    if group in ("exec", "director", "assistant") and INTRODUCTION.search(text):
        return "introduction"
    if group == "board" and not GOVERNANCE.search(text):
        return "off-topic-for-board"
    return None


FRIEND_BUSINESS = re.compile(
    r"\b(?:deal|contract|invoice|worksheet|agreement|trading|pipeline|counterparty|ferc|team|"
    r"schedul\w*|attached|vacation|update|call|meeting|markets?|corvane|"
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
    r"555-01\d\d|\(555\)|\bx55\d\d\b|@|\{\{[pqsx]:\d+:e\}\}|https?://|www\.|100 Commerce Street|"
    r"P\.O\. Box|\b79999\b|"
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


def rewrite_greeting(template: str, to_first: str | None) -> str:
    """The opening line addresses the bound recipient ("Dear Mr. X," -> "Dear Helena,")."""
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
        if len(names) > 1 or to_first is None:
            lines[idx] = f"{word or 'Hi'} all{punct if punct != '-' else ','}"
        else:
            lines[idx] = f"{word + ' ' if word else ''}{to_first}{punct}"
        return "\n".join(lines)
    if to_first and re.fullmatch(r"[A-Z]{2,3}[,:]", line):  # "JW,"
        lines[idx] = to_first + line[-1]
        return "\n".join(lines)
    inline = _INLINE_GREETING.match(line)
    if inline and to_first:
        word = inline.group("word") or ""
        lines[idx] = word + to_first + inline.group("punct") + " " + line[inline.end() :]
    return "\n".join(lines)


def reframe(template: str, *, to_first: str | None, sender_first: str | None) -> str:
    """Greeting to the bound recipient, the original sign-off and signature block removed and
    the sender's own sign-off appended (no sign-off for `sender_first=None`)."""
    body, closing = strip_signature(template)
    body = rewrite_greeting(body, to_first)
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
            or _STUB_LINE.search(raw)
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
