"""Keyset pagination with opaque cursors (stable under a fixed state: no dupes, no gaps)."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field

from mailsim.domain.errors import InvalidRequest

MAX_PAGE_SIZE = 500


@dataclass
class Page[T]:
    items: list[T] = field(default_factory=list)
    next_cursor: str | None = None


def encode_cursor(sort_key: str, item_id: str) -> str:
    raw = json.dumps([sort_key, item_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InvalidRequest("malformed page cursor") from None
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value):
        raise InvalidRequest("malformed page cursor")
    return value[0], value[1]


def check_page_size(size: int) -> int:
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise InvalidRequest(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    return size
