"""The OData query subset the Graph profile supports: $filter, $orderby, $select, $expand,
$top/$skip/$skiptoken, $count and $search parsing, plus opaque paging/delta tokens.

Everything here is pure (no engine access). Filters are compiled to predicates evaluated
over the rendered Graph message JSON, so a property's filter semantics always match what the
client reads back. Two error kinds, both HTTP 400:

- `QueryError` (ErrorInvalidRequest): the query is malformed.
- `QueryNotSupported` (ErrorNotSupported): valid OData that this sandbox does not implement.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mailsim.domain.errors import InvalidRequest

DEFAULT_TOP = 10
MAX_TOP = 1000
MAX_FILTER_CHARS = 4096
MAX_FILTER_DEPTH = 32  # nested parentheses + lambdas
MAX_TOKEN_CHARS = 256
MAX_TOKEN_INT = 2**53
_UINT = re.compile(r"[0-9]{1,10}")


class QueryError(InvalidRequest):
    """Malformed OData query (Graph: 400 ErrorInvalidRequest)."""


class QueryNotSupported(InvalidRequest):
    """Valid OData the sandbox does not implement (Graph: 400 ErrorNotSupported)."""


# --------------------------------------------------------------------------- properties

BOOL_PROPS = frozenset({"isRead", "isDraft", "hasAttachments"})
DATE_PROPS = frozenset(
    {"receivedDateTime", "sentDateTime", "createdDateTime", "lastModifiedDateTime"}
)
STRING_PROPS = frozenset(
    {
        "id", "subject", "conversationId", "parentFolderId", "importance", "internetMessageId",
        "inferenceClassification", "flag/flagStatus", "from/emailAddress/address",
        "from/emailAddress/name", "sender/emailAddress/address", "sender/emailAddress/name",
    }
)  # fmt: skip
# collection -> what a lambda variable may be compared on ("" = the item itself)
LAMBDA_PROPS: dict[str, frozenset[str]] = {
    "categories": frozenset({""}),
    "toRecipients": frozenset({"emailAddress/address", "emailAddress/name"}),
    "ccRecipients": frozenset({"emailAddress/address", "emailAddress/name"}),
    "bccRecipients": frozenset({"emailAddress/address", "emailAddress/name"}),
}
ORDER_PROPS = DATE_PROPS | {"subject", "from/emailAddress/address", "from/emailAddress/name"}
SELECTABLE = frozenset(
    {
        "id", "createdDateTime", "lastModifiedDateTime", "changeKey", "categories",
        "receivedDateTime", "sentDateTime", "hasAttachments", "internetMessageId", "subject",
        "bodyPreview", "importance", "parentFolderId", "conversationId",
        "isDeliveryReceiptRequested", "isReadReceiptRequested", "isRead", "isDraft",
        "inferenceClassification", "body", "sender", "from", "toRecipients", "ccRecipients",
        "bccRecipients", "replyTo", "flag", "internetMessageHeaders",
    }
)  # fmt: skip
EXTENDED_PROPS = "singleValueExtendedProperties"

Predicate = Callable[[Mapping[str, Any]], bool]


def path_get(obj: Any, path: str) -> Any:
    """Navigate `a/b/c` through nested dicts; None when any step is missing."""
    for part in path.split("/") if path else ():
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(part)
    return obj


def parse_datetime(text: str) -> datetime:
    value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise QueryError(f"invalid DateTimeOffset literal {text!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- $filter

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'(?:[^']|'')*')
      | (?P<dt>\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?)
      | (?P<num>-?\d+(?:\.\d+)?)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*)
      | (?P<punct>[(),:])
    )""",
    re.VERBOSE,
)
_COMPARE = frozenset({"eq", "ne", "gt", "ge", "lt", "le"})


@dataclass(frozen=True)
class _Tok:
    kind: str  # str | dt | num | name | punct
    text: str


def _tokenize(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise QueryError(f"invalid $filter syntax near {text[pos : pos + 20]!r}")
        kind = next(k for k, v in match.groupdict().items() if v is not None)
        out.append(_Tok(kind, match.group(kind)))
        pos = match.end()
    return out


@dataclass
class _Lit:
    kind: str  # str | bool | null | dt | num
    value: Any


@dataclass
class _Parser:
    toks: list[_Tok]
    pos: int = 0
    lambda_vars: dict[str, str] = field(default_factory=dict)  # var -> collection
    depth: int = 0

    def nest(self) -> None:
        self.depth += 1
        if self.depth > MAX_FILTER_DEPTH:
            raise QueryError(f"$filter nests deeper than {MAX_FILTER_DEPTH} levels")

    def peek(self) -> _Tok | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def take(self) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise QueryError("unexpected end of $filter")
        self.pos += 1
        return tok

    def expect(self, text: str) -> None:
        tok = self.take()
        if tok.text != text:
            raise QueryError(f"expected {text!r} in $filter, got {tok.text!r}")

    def keyword(self, word: str) -> bool:
        tok = self.peek()
        if tok is not None and tok.kind == "name" and tok.text == word:
            self.pos += 1
            return True
        return False

    # grammar: or := and ('or' and)* ; and := unary ('and' unary)* ; unary := '(' or ')' | term
    def parse_or(self) -> Predicate:
        parts = [self.parse_and()]
        while self.keyword("or"):
            parts.append(self.parse_and())
        if len(parts) == 1:
            return parts[0]
        return lambda doc: any(p(doc) for p in parts)

    def parse_and(self) -> Predicate:
        parts = [self.parse_unary()]
        while self.keyword("and"):
            parts.append(self.parse_unary())
        if len(parts) == 1:
            return parts[0]
        return lambda doc: all(p(doc) for p in parts)

    def parse_unary(self) -> Predicate:
        tok = self.peek()
        if tok is not None and tok.text == "(":
            self.take()
            self.nest()
            inner = self.parse_or()
            self.expect(")")
            self.depth -= 1
            return inner
        if tok is not None and tok.kind == "name" and tok.text == "not":
            raise QueryNotSupported("the 'not' operator is not supported")
        return self.parse_term()

    def parse_term(self) -> Predicate:
        tok = self.take()
        if tok.kind != "name":
            raise QueryError(f"expected a property in $filter, got {tok.text!r}")
        nxt = self.peek()
        if nxt is not None and nxt.text == "(":
            return self.parse_call(tok.text)
        op = self.take()
        if op.kind != "name" or op.text not in _COMPARE:
            if op.kind == "name" and op.text in {"has", "in", "add", "sub", "mul", "div", "mod"}:
                raise QueryNotSupported(f"the {op.text!r} operator is not supported")
            raise QueryError(f"expected a comparison operator after {tok.text!r}")
        return self.compare(tok.text, op.text, self.literal())

    def literal(self) -> _Lit:
        tok = self.take()
        if tok.kind == "str":
            return _Lit("str", tok.text[1:-1].replace("''", "'"))
        if tok.kind == "dt":
            return _Lit("dt", parse_datetime(tok.text))
        if tok.kind == "num":
            return _Lit("num", float(tok.text))
        if tok.kind == "name" and tok.text in ("true", "false"):
            return _Lit("bool", tok.text == "true")
        if tok.kind == "name" and tok.text == "null":
            return _Lit("null", None)
        raise QueryError(f"expected a literal in $filter, got {tok.text!r}")

    def property_kind(self, path: str) -> tuple[str, str]:
        """(kind, json path) for a property, honouring lambda variables."""
        head, _, rest = path.partition("/")
        if head in self.lambda_vars:
            allowed = LAMBDA_PROPS[self.lambda_vars[head]]
            if rest not in allowed:
                raise QueryNotSupported(f"filtering on {path!r} is not supported")
            return "str", f"@{head}/{rest}" if rest else f"@{head}"
        if path in BOOL_PROPS:
            return "bool", path
        if path in DATE_PROPS:
            return "dt", path
        if path in STRING_PROPS:
            return "str", path
        raise QueryNotSupported(f"filtering on {path!r} is not supported")

    def compare(self, path: str, op: str, lit: _Lit) -> Predicate:
        kind, json_path = self.property_kind(path)
        if lit.kind != "null" and lit.kind != kind:
            if not (kind == "dt" and lit.kind == "str"):
                raise QueryError(f"{path!r} cannot be compared with {lit.kind} literal")
            lit = _Lit("dt", parse_datetime(lit.value))
        if kind == "bool" and op not in ("eq", "ne"):
            raise QueryError(f"{op!r} is not valid for the boolean property {path!r}")
        if kind == "str" and op not in ("eq", "ne"):
            raise QueryNotSupported(f"{op!r} on the string property {path!r} is not supported")
        expected = lit.value.casefold() if isinstance(lit.value, str) else lit.value

        def predicate(doc: Mapping[str, Any]) -> bool:
            value = _resolve(doc, json_path)
            if kind == "dt" and isinstance(value, str):
                value = parse_datetime(value)
            elif isinstance(value, str):
                value = value.casefold()
            return _apply(op, value, expected)

        return predicate

    def parse_call(self, name: str) -> Predicate:
        head, _, fn = name.rpartition("/")
        if fn in ("any", "all") and head:
            return self.parse_lambda(head, fn)
        if name == "startswith":
            self.expect("(")
            prop = self.take()
            self.expect(",")
            lit = self.literal()
            self.expect(")")
            kind, json_path = self.property_kind(prop.text)
            if kind != "str" or lit.kind != "str":
                raise QueryError("startswith takes a string property and a string literal")
            prefix = str(lit.value).casefold()
            return lambda doc: str(_resolve(doc, json_path) or "").casefold().startswith(prefix)
        raise QueryNotSupported(f"the function {name!r} is not supported")

    def parse_lambda(self, collection: str, fn: str) -> Predicate:
        if collection not in LAMBDA_PROPS:
            raise QueryNotSupported(f"lambda operators on {collection!r} are not supported")
        self.expect("(")
        var = self.take()
        if var.kind != "name" or "/" in var.text:
            raise QueryError("expected a lambda variable")
        self.expect(":")
        self.nest()
        outer = self.lambda_vars.get(var.text)  # a nested lambda may reuse the name
        self.lambda_vars[var.text] = collection
        try:
            inner = self.parse_or()
        finally:
            if outer is None:
                del self.lambda_vars[var.text]
            else:
                self.lambda_vars[var.text] = outer
        self.expect(")")
        self.depth -= 1
        quant = any if fn == "any" else all

        def predicate(doc: Mapping[str, Any]) -> bool:
            items = doc.get(collection) or []
            return quant(inner({**doc, f"@{var.text}": item}) for item in items)

        return predicate


def _resolve(doc: Mapping[str, Any], json_path: str) -> Any:
    if json_path.startswith("@"):
        var, _, rest = json_path.partition("/")
        return path_get(doc.get(var), rest)
    return path_get(doc, json_path)


def _apply(op: str, value: Any, expected: Any) -> bool:
    if op == "eq":
        return bool(value == expected)
    if op == "ne":
        return bool(value != expected)
    if value is None or expected is None:
        return False  # ordering comparisons with null are false (OData)
    return bool({"gt": value > expected, "ge": value >= expected, "lt": value < expected,
                 "le": value <= expected}[op])  # fmt: skip


def compile_filter(text: str) -> Predicate:
    if not text.strip():
        raise QueryError("empty $filter")
    if len(text) > MAX_FILTER_CHARS:
        raise QueryError(f"$filter is longer than {MAX_FILTER_CHARS} characters")
    parser = _Parser(_tokenize(text))
    predicate = parser.parse_or()
    extra = parser.peek()
    if extra is not None:
        raise QueryError(f"unexpected {extra.text!r} in $filter")
    return predicate


# ---------------------------------------------------------------------- other options


def parse_orderby(text: str) -> list[tuple[str, bool]]:
    """[(property, descending)]; default direction is ascending (OData)."""
    out: list[tuple[str, bool]] = []
    for part in text.split(","):
        words = part.split()
        if not words or len(words) > 2 or (len(words) == 2 and words[1] not in ("asc", "desc")):
            raise QueryError(f"invalid $orderby clause {part.strip()!r}")
        if words[0] not in ORDER_PROPS:
            raise QueryNotSupported(f"ordering by {words[0]!r} is not supported")
        out.append((words[0], len(words) == 2 and words[1] == "desc"))
    return out


def parse_select(text: str) -> frozenset[str]:
    fields = [f.strip() for f in text.split(",")]
    for name in fields:
        if name not in SELECTABLE:
            raise QueryError(f"could not find a property named {name!r} on type message")
    return frozenset(fields)


EXTENDED_RE = re.compile(
    r"^singleValueExtendedProperties\(\$filter=id eq '(?P<id>[^']+)'\)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Expand:
    attachments: bool = False
    extended_ids: frozenset[str] = frozenset()  # normalised extended property ids


def normalise_extended_id(raw: str) -> str:
    return " ".join(raw.split()).casefold()


def _split_top_level(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return parts


def parse_expand(text: str) -> Expand:
    attachments, ids = False, set()
    for item in _split_top_level(text):
        if item == "attachments":
            attachments = True
            continue
        match = EXTENDED_RE.match(item)
        if match is None:
            raise QueryNotSupported(f"$expand={item!r} is not supported")
        ids.add(normalise_extended_id(match.group("id")))
    return Expand(attachments, frozenset(ids))


def parse_int(name: str, raw: str | None, *, default: int, low: int, high: int) -> int:
    if raw is None:
        return default
    if not _UINT.fullmatch(raw.strip()):
        raise QueryError(f"{name} must be a non-negative integer")
    value = int(raw)
    if not low <= value <= high:
        raise QueryError(f"{name} must be between {low} and {high}")
    return value


def parse_search(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if not text.strip():
        raise QueryError("empty $search")
    return text


# ------------------------------------------------------------------------------ tokens


def encode_token(state: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(state), separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_token(token: str, required: tuple[str, ...]) -> dict[str, Any]:
    """Decode a paging/delta token; `required` keys must be integers in [0, 2**53]."""
    if len(token) > MAX_TOKEN_CHARS:
        raise QueryError("malformed state token")
    try:
        value = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError):
        raise QueryError("malformed state token") from None
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), int)
        or isinstance(value.get(k), bool)
        or not 0 <= value[k] <= MAX_TOKEN_INT
        for k in required
    ):
        raise QueryError("malformed state token")
    return value


__all__ = [
    "DEFAULT_TOP", "EXTENDED_PROPS", "Expand", "MAX_TOP", "Predicate", "QueryError",
    "QueryNotSupported", "compile_filter", "decode_token", "encode_token",
    "normalise_extended_id", "parse_datetime", "parse_expand", "parse_int", "parse_orderby",
    "parse_search", "parse_select", "path_get",
]  # fmt: skip
