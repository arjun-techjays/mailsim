"""Virtual clock: the only time source inside the engine (wall clock is never read)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mailsim.domain.errors import InvalidRequest


def parse_ts(value: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidRequest(f"invalid timestamp {value!r}") from None
    if dt.tzinfo is None:
        raise InvalidRequest(f"timestamp must be timezone-aware: {value!r}")
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class VirtualClock:
    def __init__(self, now: datetime) -> None:
        self.now = parse_ts(now)

    def advance(self, seconds: float) -> datetime:
        if seconds < 0:
            raise InvalidRequest("clock cannot move backwards")
        self.now = self.now + timedelta(seconds=seconds)
        return self.now

    def advance_to(self, target: datetime) -> datetime:
        target = parse_ts(target)
        if target < self.now:
            raise InvalidRequest("clock cannot move backwards")
        self.now = target
        return self.now
