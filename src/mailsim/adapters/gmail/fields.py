"""Partial responses: Google's ``fields`` parameter (``a,b/c,d(e,f)``, ``*`` = everything)."""

from __future__ import annotations

import re
from typing import Any

from mailsim.domain.errors import InvalidRequest

type Tree = dict[str, Tree | None]  # None = keep the whole value
_TOKEN = re.compile(r"\s*([A-Za-z0-9_@*.-]+|[,/()])")


def _tokens(spec: str) -> list[str]:
    out, pos = [], 0
    while pos < len(spec):
        if spec[pos:].strip() == "":
            break
        m = _TOKEN.match(spec, pos)
        if m is None:
            raise InvalidRequest(f"Invalid field selection {spec!r}")
        out.append(m.group(1))
        pos = m.end()
    return out


def parse_fields(spec: str) -> Tree:
    tokens = _tokens(spec)
    pos = 0

    def bad() -> InvalidRequest:
        return InvalidRequest(f"Invalid field selection {spec!r}")

    def merge(into: Tree, key: str, sub: Tree | None) -> None:
        existing = into.get(key, {})
        if key not in into:
            into[key] = sub
        elif existing is None or sub is None:
            into[key] = None  # a whole-value selection wins over a narrower one
        else:
            for k, v in sub.items():
                merge(existing, k, v)

    def selection() -> Tree:
        nonlocal pos
        tree: Tree = {}
        while True:
            if pos >= len(tokens) or tokens[pos] in ",/()":
                raise bad()
            path = [tokens[pos]]
            pos += 1
            while pos < len(tokens) and tokens[pos] == "/":
                if pos + 1 >= len(tokens) or tokens[pos + 1] in ",/()":
                    raise bad()
                path.append(tokens[pos + 1])
                pos += 2
            sub: Tree | None = None
            if pos < len(tokens) and tokens[pos] == "(":
                pos += 1
                sub = selection()
                if pos >= len(tokens) or tokens[pos] != ")":
                    raise bad()
                pos += 1
            for name in reversed(path[1:]):
                sub = {name: sub}
            merge(tree, path[0], sub)
            if pos < len(tokens) and tokens[pos] == ",":
                pos += 1
                continue
            return tree

    tree = selection()
    if pos != len(tokens):
        raise bad()
    return tree


def apply_fields(data: Any, tree: Tree | None) -> Any:
    if tree is None or "*" in tree:
        return data
    if isinstance(data, list):
        return [apply_fields(item, tree) for item in data]
    if isinstance(data, dict):
        return {k: apply_fields(data[k], sub) for k, sub in tree.items() if k in data}
    return data


def select(data: dict[str, Any], spec: str | None) -> dict[str, Any]:
    if not spec or not spec.strip():
        return data
    out: dict[str, Any] = apply_fields(data, parse_fields(spec))
    return out
