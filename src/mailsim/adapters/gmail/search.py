"""A documented subset of Gmail search syntax, compiled to predicates over canonical messages.

Grammar (case-insensitive keywords)::

    query   := or_expr
    or_expr := and_expr ("OR" and_expr)*
    and_expr:= unary (["AND"] unary)*          (juxtaposition = AND)
    unary   := "-" unary | atom
    atom    := "(" or_expr ")" | "{" unary* "}"  (braces = OR group) | term
    term    := operator ":" (word | "quoted" | "(" word* ")") | "quoted phrase" | word

Operators: from to cc bcc subject label in is has filename after before older newer
older_than newer_than rfc822msgid. Free text (words and phrases) is a case-insensitive
substring match over subject, plain-text body and sender address (the same fields as the
neutral engine search). Other Gmail operators are refused with INVALID_REQUEST.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mailsim.adapters.gmail.labels import folder_type, is_unread, user_labels
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import CanonicalMessage

SUPPORTED = frozenset({"from", "to", "cc", "bcc", "subject", "label", "in", "is", "has",
                       "filename", "after", "before", "older", "newer", "older_than",
                       "newer_than", "rfc822msgid"})  # fmt: skip
UNSUPPORTED = frozenset({"deliveredto", "category", "size", "larger", "smaller", "list", "circle",
                         "label_id", "near", "around", "is_not"})  # fmt: skip
IN_VALUES = frozenset({"inbox", "sent", "drafts", "draft", "trash", "archive", "anywhere", "spam"})
IS_VALUES = frozenset({"unread", "read", "starred", "unstarred", "draft"})
HAS_VALUES = frozenset({"attachment", "userlabels", "nouserlabels"})
_STOP = set('(){}"')
_RELATIVE = re.compile(r"^([0-9]{1,6})([dmy])$")
MAX_QUERY_CHARS = 2048
MAX_DEPTH = 32
MAX_DAYS = 100 * 365
_EPOCH_MAX = 4102444800  # 2100-01-01
_DAYS = {"d": 1, "m": 30, "y": 365}
_SYSTEM_LABEL_NAMES = {"inbox": "inbox", "sent": "sent", "draft": "drafts", "drafts": "drafts",
                       "trash": "trash"}  # fmt: skip


@dataclass(frozen=True)
class Term:
    op: str | None  # None = free text
    value: str
    phrase: bool = False


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


# ------------------------------------------------------------------------------- tokenizer


def _bad(message: str) -> InvalidRequest:
    return InvalidRequest(f"Invalid search query: {message}")


def _quoted(q: str, i: int) -> tuple[str, int]:
    end = q.find('"', i + 1)
    if end < 0:
        raise _bad("unterminated quote")
    return q[i + 1 : end], end + 1


def _word(q: str, i: int) -> tuple[str, int]:
    j = i
    while j < len(q) and not q[j].isspace() and q[j] not in _STOP:
        j += 1
    return q[i:j], j


def _validate(op: str, value: str) -> str:
    v = value.strip().lower()
    if not v:
        raise _bad(f"missing value for {op}:")
    checks: dict[str, Callable[[str], bool]] = {
        "in": IN_VALUES.__contains__, "is": IS_VALUES.__contains__,
        "has": HAS_VALUES.__contains__,
    }  # fmt: skip
    if op in checks and not checks[op](v):
        raise _bad(f"unsupported value {op}:{value}")
    if op in ("after", "before", "older", "newer"):
        parse_date(v)
    if op in ("older_than", "newer_than"):
        relative_days(v)
    return value.strip()


def tokenize(q: str) -> list[object]:
    """Tokens: '(' ')' '{' '}' '-' 'OR' 'AND' and Term leaves."""
    out: list[object] = []
    i = 0
    while i < len(q):
        c = q[i]
        if c.isspace():
            i += 1
        elif c in "(){}":
            out.append(c)
            i += 1
        elif c == "-" and i + 1 < len(q) and not q[i + 1].isspace():
            out.append("-")
            i += 1
        elif c == '"':
            text, i = _quoted(q, i)
            if not text.strip():
                raise _bad("empty phrase")
            out.append(Term(None, text, True))
        else:
            word, i = _word(q, i)
            head, sep, value = word.partition(":")
            if sep and not value and head.lower() in SUPPORTED and i < len(q) and q[i] in '"(':
                values, i = _operator_value(q, i, head.lower())
                out += values
            else:
                out.append(_classify(word))
    return out


def _classify(word: str) -> object:
    if word.upper() in ("OR", "AND"):
        return word.upper()
    if word.upper() == "AROUND":
        raise _bad("unsupported operator AROUND")
    head, sep, value = word.partition(":")
    op = head.lower()
    if sep and op in UNSUPPORTED:
        raise _bad(f"unsupported operator {head}:")
    if sep and op in SUPPORTED:
        return Term(op, _validate(op, value))
    return Term(None, word)


def _operator_value(q: str, i: int, op: str) -> tuple[list[object], int]:
    if q[i] == '"':
        text, i = _quoted(q, i)
        return [Term(op, _validate(op, text), True)], i
    end = q.find(")", i)
    if end < 0:
        raise _bad(f"unterminated group after {op}:")
    words = q[i + 1 : end].split()
    if not words:
        raise _bad(f"empty group after {op}:")
    return ["(", *(Term(op, _validate(op, w)) for w in words), ")"], end + 1


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

    def or_expr(self, depth: int = 0) -> Node:
        if depth > MAX_DEPTH:
            raise _bad(f"nesting deeper than {MAX_DEPTH}")
        nodes = [self.and_expr(depth)]
        while self.peek() == "OR":
            self.take()
            nodes.append(self.and_expr(depth))
        return nodes[0] if len(nodes) == 1 else Or(tuple(nodes))

    def and_expr(self, depth: int) -> Node:
        nodes: list[Node] = []
        while self.peek() not in (None, ")", "}", "OR"):
            if self.peek() == "AND":
                self.take()
                continue
            nodes.append(self.unary(depth))
        if not nodes:
            raise _bad("expected a search term")
        return nodes[0] if len(nodes) == 1 else And(tuple(nodes))

    def unary(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise _bad(f"nesting deeper than {MAX_DEPTH}")
        if self.peek() == "-":
            self.take()
            return Not(self.unary(depth + 1))
        return self.atom(depth)

    def atom(self, depth: int) -> Node:
        tok = self.take()
        if tok == "(":
            node = self.or_expr(depth + 1)
            if self.take() != ")":
                raise _bad("missing ')'")
            return node
        if tok == "{":
            nodes: list[Node] = []
            while self.peek() not in (None, "}"):
                nodes.append(self.unary(depth + 1))
            if self.take() != "}" or not nodes:
                raise _bad("unbalanced or empty '{...}' group")
            return nodes[0] if len(nodes) == 1 else Or(tuple(nodes))
        if isinstance(tok, Term):
            return tok
        raise _bad(f"unexpected {tok!r}")


def parse_query(q: str) -> Node | None:
    """Parse a Gmail `q` string. None means "match everything" (blank query)."""
    if len(q) > MAX_QUERY_CHARS:
        raise _bad(f"longer than {MAX_QUERY_CHARS} characters")
    if not q.strip():
        return None
    parser = _Parser(tokenize(q))
    node = parser.or_expr()
    if parser.peek() is not None:
        raise _bad(f"unexpected {parser.peek()!r}")
    return node


def render(node: Node) -> str:
    """Inverse of parse_query (canonical form); used for logging and property tests."""
    if isinstance(node, Term):
        value = f'"{node.value}"' if node.phrase else node.value
        return f"{node.op}:{value}" if node.op else value
    if isinstance(node, Not):
        return "-" + render(node.node)
    joiner = " " if isinstance(node, And) else " OR "
    return "(" + joiner.join(render(n) for n in node.nodes) + ")"


def terms(node: Node | None) -> list[tuple[Term, bool]]:
    """Every leaf with whether it is a positive top-level conjunct."""
    if node is None:
        return []
    out: list[tuple[Term, bool]] = []

    def walk(n: Node, top: bool) -> None:
        if isinstance(n, Term):
            out.append((n, top))
        elif isinstance(n, Not):
            walk(n.node, False)
        else:
            for child in n.nodes:
                walk(child, top and isinstance(n, And))

    walk(node, True)
    return out


def includes_trash(node: Node | None) -> bool:
    """Gmail searches skip Trash unless the query names it (in:trash, in:anywhere, label:trash)."""
    return any(t.op in ("in", "label") and t.value.lower() in ("trash", "anywhere")
               for t, _ in terms(node))  # fmt: skip


# -------------------------------------------------------------------------------- matching


def relative_days(value: str) -> int:
    m = _RELATIVE.match(value)
    days = int(m.group(1)) * _DAYS[m.group(2)] if m else -1
    if not 0 <= days <= MAX_DAYS:
        raise _bad(f"expected <number><d|m|y> up to 100 years, got {value!r}")
    return days


def parse_date(value: str) -> datetime:
    """YYYY/MM/DD, YYYY-MM-DD or epoch seconds (1970..2100), read as UTC (Gmail uses the
    account zone)."""
    if value.isascii() and value.isdigit() and 8 < len(value) <= 10 and int(value) <= _EPOCH_MAX:
        return datetime.fromtimestamp(int(value), UTC)
    for fmt in ("%Y/%m/%d", "%Y-%m-%d") if value.isascii() else ():
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except (ValueError, OverflowError):
            continue
    raise _bad(f"invalid date {value!r} (use YYYY/MM/DD)")


@dataclass
class Context:
    me: Callable[[], str]
    now: Callable[[], datetime]


def message_date(msg: CanonicalMessage) -> datetime:
    return msg.received_at or msg.created_at


def _addresses(msg: CanonicalMessage, field: str) -> list[str]:
    addrs = [msg.from_] if field == "from" else getattr(msg, field)
    return [f"{a.email} {a.name or ''}".lower() for a in addrs]


def _norm_label(name: str) -> str:
    return re.sub(r"[\s/]+", "-", name.strip().lower())


def _term(t: Term, msg: CanonicalMessage, ctx: Context) -> bool:
    v = t.value.lower()
    if t.op is None:
        hay = f"{msg.subject}\n{msg.body_text or ''}\n{msg.from_.email}".lower()
        return v in hay
    if t.op in ("from", "to", "cc", "bcc"):
        needle = ctx.me().lower() if v == "me" else v
        return any(needle in a for a in _addresses(msg, t.op))
    if t.op == "subject":
        return v in msg.subject.lower()
    if t.op == "rfc822msgid":
        return v.strip("<>") == msg.message_id_header.lower().strip("<>")
    if t.op == "filename":
        return any(v in a.filename.lower() for a in msg.attachments)
    if t.op == "label" and v in ("unread", "starred", "read"):
        return _term(Term("is", v), msg, ctx)
    if t.op in ("in", "label"):
        return _in(t.op, v, msg)
    if t.op == "is":
        return {"unread": is_unread(msg), "read": not is_unread(msg), "draft": msg.is_draft,
                "starred": "FLAGGED" in msg.flags,
                "unstarred": "FLAGGED" not in msg.flags}[v]  # fmt: skip
    if t.op == "has":
        own = user_labels(msg)
        return {"attachment": bool(msg.attachments), "userlabels": bool(own),
                "nouserlabels": not own}[v]  # fmt: skip
    return _date(t.op, v, msg, ctx)


def _in(op: str, v: str, msg: CanonicalMessage) -> bool:
    if v == "anywhere":
        return True
    folder = _SYSTEM_LABEL_NAMES.get(v) or (v if op == "in" else None)
    if folder is not None:
        return folder_type(msg) == folder
    return any(_norm_label(n) == _norm_label(v) for n in user_labels(msg))


def _date(op: str, v: str, msg: CanonicalMessage, ctx: Context) -> bool:
    when = message_date(msg)
    if op in ("after", "newer"):
        return when >= parse_date(v)
    if op in ("before", "older"):
        return when < parse_date(v)
    cutoff = ctx.now() - timedelta(days=relative_days(v))
    return when < cutoff if op == "older_than" else when > cutoff


def matches(node: Node | None, msg: CanonicalMessage, ctx: Context) -> bool:
    if node is None:
        return True
    if isinstance(node, Term):
        return _term(node, msg, ctx)
    if isinstance(node, Not):
        return not matches(node.node, msg, ctx)
    if isinstance(node, And):
        return all(matches(n, msg, ctx) for n in node.nodes)
    return any(matches(n, msg, ctx) for n in node.nodes)
