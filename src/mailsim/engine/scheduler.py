"""Deterministic ordering of due events: (due_at, declaration order, id)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Scheduled:
    id: str
    due_at: datetime | None
    order_key: int
    status: str  # pending | armed | fired


def due_in_order(items: list[Scheduled], now: datetime) -> list[Scheduled]:
    """Events that are due at or before `now`, in the order they must fire.

    Only items with a concrete `due_at` and status in (pending, armed) are eligible.
    """
    eligible = [
        i
        for i in items
        if i.due_at is not None and i.status in ("pending", "armed") and i.due_at <= now
    ]
    return sorted(eligible, key=lambda i: (i.due_at, i.order_key, i.id))
