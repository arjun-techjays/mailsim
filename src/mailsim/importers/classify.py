"""Transparent keyword classifier for mail topics (no model, no external service).

Each topic has weighted keyword patterns; the score of a topic is the sum of the weights of
the distinct patterns that match (case-insensitive, word-bounded). The highest score wins;
ties go to the earlier topic in `TOPICS`. A message matching nothing is ``general``.
`classify` returns the scores and the matched keywords so every decision can be audited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOPICS = (
    "board", "legal", "finance", "deals", "hr", "vendor", "scheduling", "ops", "personal",
    "newsletter",
)  # fmt: skip
GENERAL = "general"

# (pattern, weight). Patterns are regex fragments matched with word boundaries.
KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    "board": (
        (r"board of directors", 4),
        (r"board meeting", 4),
        (r"board members?", 3),
        (r"bod", 2),
        (r"audit committee", 3),
        (r"compensation committee", 3),
        (r"shareholders?", 2),
        (r"governance", 2),
        (r"chairman", 2),
        (r"annual meeting", 2),
        (r"proxy", 2),
        (r"board resolution", 3),
        (r"minutes", 1),
        (r"directors", 1),
        (r"board", 1),
    ),
    "legal": (
        (r"counsel", 2),
        (r"attorneys?", 2),
        (r"lawyers?", 2),
        (r"legal", 2),
        (r"litigation", 3),
        (r"lawsuit", 3),
        (r"settlement", 2),
        (r"regulatory", 2),
        (r"ferc", 2),
        (r"compliance", 2),
        (r"confidentiality agreement", 3),
        (r"nda", 2),
        (r"isda", 3),
        (r"master agreement", 3),
        (r"indemni\w*", 3),
        (r"clause", 2),
        (r"amendment", 2),
        (r"subpoena", 3),
        (r"privileged", 2),
        (r"contract", 1),
        (r"agreement", 1),
        (r"guarant\w+", 2),
    ),
    "finance": (
        (r"budget", 3),
        (r"forecast", 2),
        (r"invoices?", 2),
        (r"payments?", 2),
        (r"audit", 2),
        (r"earnings", 3),
        (r"revenue", 2),
        (r"expenses?", 2),
        (r"capital", 1),
        (r"cash", 2),
        (r"credit", 1),
        (r"financial", 2),
        (r"accounting", 2),
        (r"quarter(ly)?", 2),
        (r"tax", 2),
        (r"treasury", 3),
        (r"wire transfer", 3),
        (r"loan", 2),
        (r"debt", 2),
        (r"write-?down", 3),
        (r"reserves?", 1),
        (r"p&l", 3),
        (r"balance sheet", 3),
        (r"cost center", 3),
        (r"reimburse\w*", 2),
        (r"accounts payable", 3),
        (r"cfo", 1),
        (r"\$\s?\d[\d,.]*\s?(?:mm|million|k)", 1),
    ),
    "deals": (
        (r"deals?", 2),
        (r"transaction", 2),
        (r"acquisition", 3),
        (r"merger", 3),
        (r"partnership", 1),
        (r"proposal", 1),
        (r"bid", 1),
        (r"negotiat\w+", 2),
        (r"term sheet", 3),
        (r"customers?", 1),
        (r"origination", 2),
        (r"joint venture", 3),
        (r"valuation", 2),
        (r"due diligence", 3),
        (r"letter of intent", 3),
        (r"counterpart(y|ies)", 1),
    ),
    "hr": (
        (r"interview\w*", 2),
        (r"resumes?", 3),
        (r"candidates?", 2),
        (r"hir(e|ed|ing)", 2),
        (r"recruit\w*", 2),
        (r"offer letter", 3),
        (r"performance review", 3),
        (r"prc", 2),
        (r"compensation", 1),
        (r"bonus(es)?", 2),
        (r"salary", 2),
        (r"benefits", 1),
        (r"promotion", 2),
        (r"interns?(hip)?", 2),
        (r"analyst program", 2),
        (r"associate program", 2),
        (r"headcount", 2),
        (r"human resources", 3),
    ),
    "vendor": (
        (r"vendors?", 3),
        (r"suppliers?", 3),
        (r"purchase order", 3),
        (r"procurement", 3),
        (r"quotes?", 1),
        (r"licen[cs]e", 2),
        (r"renewal", 2),
        (r"contractors?", 2),
        (r"consultants?", 1),
        (r"service agreement", 3),
        (r"software", 1),
        (r"maintenance", 1),
        (r"statement of work", 3),
        (r"sow", 2),
    ),
    "scheduling": (
        (r"meeting", 1),
        (r"schedule[ds]?", 2),
        (r"calendar", 2),
        (r"conference call", 2),
        (r"available", 1),
        (r"availability", 2),
        (r"reschedul\w+", 3),
        (r"lunch", 2),
        (r"dinner", 1),
        (r"agenda", 1),
        (r"conference room", 2),
        (r"appointment", 2),
        (r"flight", 1),
        (r"itinerary", 2),
        (r"travel", 1),
        (r"\d{1,2}:\d{2}\s?(am|pm)", 1),
    ),
    "ops": (
        (r"pipeline", 2),
        (r"gas", 1),
        (r"power", 1),
        (r"trading", 1),
        (r"desk", 1),
        (r"operations", 2),
        (r"outage", 3),
        (r"systems?", 1),
        (r"deploy\w*", 2),
        (r"migration", 2),
        (r"servers?", 2),
        (r"capacity", 2),
        (r"curves?", 1),
        (r"volumes?", 1),
        (r"nominations?", 2),
        (r"plant", 1),
        (r"generation", 1),
        (r"positions?", 1),
        (r"model(s|ing)?", 1),
        (r"risk", 1),
        (r"database", 2),
    ),
    "personal": (
        (r"family", 2),
        (r"kids", 2),
        (r"wife", 2),
        (r"husband", 2),
        (r"birthday", 3),
        (r"vacation", 2),
        (r"weekend", 1),
        (r"golf", 2),
        (r"party", 1),
        (r"wedding", 3),
        (r"baby", 2),
        (r"church", 2),
        (r"football", 2),
        (r"congratulations", 2),
        (r"holidays?", 1),
        (r"tickets?", 1),
    ),
    "newsletter": (
        (r"unsubscribe", 4),
        (r"newsletter", 3),
        (r"click here", 3),
        (r"subscri\w+", 2),
        (r"this week", 1),
        (r"edition", 2),
        (r"mailing list", 3),
        (r"press release", 3),
        (r"all employees", 2),
        (r"announc\w+", 1),
        (r"digest", 2),
        (r"news", 1),
    ),
}

_COMPILED: dict[str, tuple[tuple[re.Pattern[str], str, int], ...]] = {
    topic: tuple((re.compile(rf"(?<![\w]){p}(?![\w])", re.IGNORECASE), p, w) for p, w in pats)
    for topic, pats in KEYWORDS.items()
}


@dataclass(frozen=True)
class Classification:
    topic: str
    scores: dict[str, int]
    keywords: dict[str, list[str]]


def classify(text: str) -> Classification:
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for topic in TOPICS:
        hits = [(p, w) for rx, p, w in _COMPILED[topic] if rx.search(text)]
        if hits:
            scores[topic] = sum(w for _, w in hits)
            matched[topic] = [p for p, _ in hits]
    if not scores:
        return Classification(GENERAL, {}, {})
    best = max(TOPICS, key=lambda t: (scores.get(t, 0), -TOPICS.index(t)))
    return Classification(best, scores, matched)
