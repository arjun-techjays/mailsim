"""The KQL subset of Graph `$search` on messages, compiled to predicates over canonical
messages (pure: no engine access).

Grammar (keywords upper-case, property names case-insensitive)::

    query    := or_expr
    or_expr  := and_expr ("OR" and_expr)*
    and_expr := unary (["AND"] unary)*            (juxtaposition = AND)
    unary    := "NOT" unary | "-" unary | atom
    atom     := "(" or_expr ")" | term
    term     := prop ":" value | "received" ("<"|"<="|">"|">="|"=") date
              | "received:" date ".." date | "phrase" | word

Properties: subject, body, from, to, cc, bcc, participants (from/to/cc/bcc), attachment
(file name), hasAttachments / hasAttachment (true|false), isRead (true|false), received
(YYYY-MM-DD or an ISO 8601 date-time, UTC). Text values match as case-insensitive
substrings; free text matches subject, plain-text body and sender address (the same fields as
the neutral search). Any other `name:value` term is refused with ErrorInvalidRequest rather
than silently matching nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from mailsim.adapters.graph.odata import QueryError
from mailsim.domain.models import CanonicalMessage

MAX_SEARCH_CHARS = 1024
MAX_DEPTH = 32
TEXT_PROPS = frozenset({"subject", "body", "from", "to", "cc", "bcc", "participants",
                        "attachment"})  # fmt: skip
BOOL_PROPS = {"hasattachments": "hasattachments", "hasattachment": "hasattachments",
              "isread": "isread"}  # fmt: skip
_TERM = re.compile(r"^(?P<prop>[A-Za-z]+)(?P<op>:|<=|>=|<|>|=)(?P<value>.*)$", re.DOTALL)
_STOP = set('()"')


@dataclass(frozen=True)
class Term:
    prop: str | None  # None = free text
    op: str  # ":" for text / bool / single-day; comparison for received
    value: str


@dataclass(frozen=True)
class Not:
    node: Node


@dataclass(frozen=True)
class And:
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    nodes: tuple[Node, ...]


type Node = Term | Not | And | Or


@dataclass(frozen=True)
class Search:
    """A parsed `$search`: `text` for a plain substring query (no KQL syntax at all), else
    `node`. `anchor` is a positive top-level free-text term usable as an engine search."""

    text: str | None
    node: Node | None
    anchor: str | None


def _bad(message: str) -> QueryError:
    return QueryError(f"Invalid $search: {message}")


def _unquote(raw: str) -> str:
    """Graph wants the whole value quoted (`$search="..."`, inner quotes as \\")."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        inner = text[1:-1]
        if '"' not in inner.replace('\\"', ""):
            return inner.replace('\\"', '"')
    return text


# ------------------------------------------------------------------------------- tokenizer


def _quoted(q: str, i: int) -> tuple[str, int]:
    end = q.find('"', i + 1)
    if end < 0:
        raise _bad("unterminated quote")
    return q[i + 1 : end], end + 1


def tokenize(q: str) -> list[object]:
    """Tokens: '(' ')' 'NOT' 'AND' 'OR' and Term leaves. A property value may be quoted."""
    out: list[object] = []
    i = 0
    while i < len(q):
        c = q[i]
        if c.isspace():
            i += 1
        elif c in "()":
            out.append(c)
            i += 1
        elif c == "-" and i + 1 < len(q) and not q[i + 1].isspace():
            out.append("NOT")
            i += 1
        elif c == '"':
            text, i = _quoted(q, i)
            if not text.strip():
                raise _bad("empty phrase")
            out.append(Term(None, ":", text))
        else:
            j = i
            while j < len(q) and not q[j].isspace() and q[j] not in _STOP:
                j += 1
            word = q[i:j]
            if word.endswith((":", "<", ">", "=")) and j < len(q) and q[j] == '"':
                value, j = _quoted(q, j)
                term = _property(word + value)
                if term is None:
                    raise _bad(f"unexpected {word!r}")
                out.append(term)
            else:
                out.append(_classify(word))
            i = j
    return out


def _classify(word: str) -> object:
    if word in ("AND", "OR", "NOT"):
        return word
    term = _property(word)
    return term if term is not None else Term(None, ":", word)


def _property(word: str) -> Term | None:
    m = _TERM.match(word)
    if m is None:
        return None
    prop, op, value = m.group("prop").lower(), m.group("op"), m.group("value").strip()
    if prop not in TEXT_PROPS and prop not in BOOL_PROPS and prop != "received":
        if op != ":" or not value or value.startswith("//"):
            return None  # "re:", "a=b", URLs and similar are ordinary words
        raise _bad(f"unsupported property {m.group('prop')!r} (supported: subject, body, "
                   "from, to, cc, bcc, participants, attachment, hasAttachments, isRead, "
                   "received)")  # fmt: skip
    if not value:
        raise _bad(f"missing value for {prop}")
    if prop == "received":
        _received_bounds(op, value)  # validate now
        return Term(prop, op, value)
    if op != ":":
        raise _bad(f"{prop} takes ':' (comparisons are only for received)")
    if prop in BOOL_PROPS:
        if value.lower() not in ("true", "false"):
            raise _bad(f"{prop} must be true or false")
        return Term(BOOL_PROPS[prop], ":", value.lower())
    return Term(prop, ":", value)


# ---------------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, tokens: list[object]) -> None:
        self.tokens, self.pos = tokens, 0

    def peek(self) -> object:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> object:
        tok = self.peek()
        self.pos += 1
        return tok

    def or_expr(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise _bad(f"nesting deeper than {MAX_DEPTH}")
        nodes = [self.and_expr(depth)]
        while self.peek() == "OR":
            self.take()
            nodes.append(self.and_expr(depth))
        return nodes[0] if len(nodes) == 1 else Or(tuple(nodes))

    def and_expr(self, depth: int) -> Node:
        nodes: list[Node] = []
        while self.peek() not in (None, ")", "OR"):
            if self.peek() == "AND":
                self.take()
                continue
            nodes.append(self.unary(depth))
        if not nodes:
            raise _bad("expected a search term")
        return nodes[0] if len(nodes) == 1 else And(tuple(nodes))

    def unary(self, depth: int) -> Node:
        if self.peek() == "NOT":
            if depth > MAX_DEPTH:
                raise _bad(f"nesting deeper than {MAX_DEPTH}")
            self.take()
            return Not(self.unary(depth + 1))
        tok = self.take()
        if tok == "(":
            node = self.or_expr(depth + 1)
            if self.take() != ")":
                raise _bad("missing ')'")
            return node
        if isinstance(tok, Term):
            return tok
        raise _bad(f"unexpected {tok!r}")


def _plain(tokens: list[object]) -> bool:
    return all(isinstance(t, Term) and t.prop is None for t in tokens)


def parse(raw: str) -> Search:
    text = _unquote(raw)
    if len(text) > MAX_SEARCH_CHARS:
        raise _bad(f"longer than {MAX_SEARCH_CHARS} characters")
    if not text.strip():
        raise _bad("empty $search")
    tokens = tokenize(text)
    if _plain(tokens):  # no KQL syntax at all: one substring search, as before
        return Search(text, None, None)
    parser = _Parser(tokens)
    node = parser.or_expr(0)
    if parser.peek() is not None:
        raise _bad(f"unexpected {parser.peek()!r}")
    return Search(None, node, _anchor(node))


def _anchor(node: Node) -> str | None:
    if isinstance(node, Term):
        return node.value if node.prop is None else None
    if isinstance(node, And):
        return next((a for a in map(_anchor, node.nodes) if a is not None), None)
    return None


# -------------------------------------------------------------------------------- matching


def _day(value: str) -> tuple[datetime, datetime]:
    """[start, end) of a date, or a single instant for a full date-time."""
    try:
        if "T" not in value and len(value) == 10:
            d = date.fromisoformat(value)
            start = datetime(d.year, d.month, d.day, tzinfo=UTC)
            return start, start + timedelta(days=1)
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise _bad(f"invalid date {value!r} (use YYYY-MM-DD or an ISO 8601 date-time)") from None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return moment, moment + timedelta(microseconds=1)


def _received_bounds(op: str, value: str) -> tuple[datetime | None, datetime | None]:
    """[low, high) of `received` for the term."""
    if op == ":" and ".." in value:
        lo, _, hi = value.partition("..")
        return _day(lo.strip())[0], _day(hi.strip())[1]
    start, end = _day(value)
    bounds: dict[str, tuple[datetime | None, datetime | None]] = {
        ":": (start, end), "=": (start, end), ">=": (start, None), ">": (end, None),
        "<": (None, start), "<=": (None, end),
    }  # fmt: skip
    return bounds[op]


def _text(m: CanonicalMessage, prop: str) -> list[str]:
    if prop == "subject":
        return [m.subject]
    if prop == "body":
        return [m.body_text or "", m.body_html or ""]
    if prop == "attachment":
        return [a.filename for a in m.attachments]
    people = {"from": [m.from_], "to": m.to, "cc": m.cc, "bcc": m.bcc,
              "participants": [m.from_, *m.to, *m.cc, *m.bcc]}[prop]  # fmt: skip
    return [f"{a.email} {a.name or ''}" for a in people]


def _term(t: Term, m: CanonicalMessage) -> bool:
    v = t.value.lower()
    if t.prop is None:
        return v in f"{m.subject}\n{m.body_text or ''}\n{m.from_.email}".lower()
    if t.prop == "hasattachments":
        return bool(m.attachments) == (v == "true")
    if t.prop == "isread":
        return ("SEEN" in m.flags) == (v == "true")
    if t.prop == "received":
        lo, hi = _received_bounds(t.op, t.value)
        when = m.received_at or m.created_at
        return (lo is None or when >= lo) and (hi is None or when < hi)
    return any(v in s.lower() for s in _text(m, t.prop))


def matches(node: Node, m: CanonicalMessage) -> bool:
    if isinstance(node, Term):
        return _term(node, m)
    if isinstance(node, Not):
        return not matches(node.node, m)
    if isinstance(node, And):
        return all(matches(n, m) for n in node.nodes)
    return any(matches(n, m) for n in node.nodes)


def predicate(node: Node) -> Callable[[CanonicalMessage], bool]:
    return lambda m: matches(node, m)


__all__ = ["Search", "matches", "parse", "predicate"]
