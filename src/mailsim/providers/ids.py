"""Deterministic provider-facing identifiers derived from internal ids.

Gmail ids look like 16 lowercase hex chars; Graph ids are long base64url strings. They are
stable for the lifetime of the internal object (spec 5.5 identity mapping) and never equal
to the internal id or the RFC Message-ID.
"""

from __future__ import annotations

import base64
import hashlib


def gmail_id(internal_id: str) -> str:
    return hashlib.sha256(f"gmail:{internal_id}".encode()).hexdigest()[:16]


def graph_id(internal_id: str) -> str:
    digest = hashlib.sha256(f"graph:{internal_id}".encode()).digest()
    return "AAMk" + base64.urlsafe_b64encode(digest).decode().rstrip("=")
