"""Opaque cursors for REST pagination (stable under a fixed state: no dupes, no gaps).

- Event trace: keyset on the gapless event ``sequence``.
- Searched message lists: keyset on (created_at, id), the same order and cursor format the
  engine uses for its own paged listing, so a cursor means the same thing either way.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime

from mailsim.domain.clock import iso, parse_ts
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.models import CanonicalMessage
from mailsim.engine.pagination import Page, decode_cursor, encode_cursor


def encode_sequence_cursor(sequence: int) -> str:
    raw = json.dumps({"after": sequence}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_sequence_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InvalidRequest("malformed page cursor") from None
    after = value.get("after") if isinstance(value, dict) else None
    if type(after) is not int or after < 0:
        raise InvalidRequest("malformed page cursor")
    return after


def _key(msg: CanonicalMessage) -> tuple[datetime, str]:
    return parse_ts(msg.created_at), msg.id


def page_messages(
    items: Sequence[CanonicalMessage], page_size: int, cursor: str | None
) -> Page[CanonicalMessage]:
    """Keyset-paginate an in-memory result (already ordered by created_at, id)."""
    ordered = sorted(items, key=_key)
    if cursor is not None:
        stamp, last_id = decode_cursor(cursor)
        after = (parse_ts(stamp), last_id)
        ordered = [m for m in ordered if _key(m) > after]
    page = ordered[:page_size]
    nxt = None
    if len(ordered) > page_size:
        nxt = encode_cursor(iso(page[-1].created_at), page[-1].id)
    return Page(page, nxt)
