from datetime import UTC, datetime, timedelta

import pytest

from mailsim.domain.clock import VirtualClock, iso, parse_ts
from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.engine.scheduler import Scheduled, due_in_order
from mailsim.store.blob import FilesystemBlobStore


def test_blob_roundtrip_idempotent_and_integrity(tmp_path):
    store = FilesystemBlobStore(tmp_path / "blobs")
    key = store.put(b"hello")
    assert store.put(b"hello") == key and store.exists(key) and store.get(key) == b"hello"
    assert not store.exists("0" * 64)
    with pytest.raises(NotFound):
        store.get("0" * 64)
    (tmp_path / "blobs" / key[:2] / key).write_bytes(b"tampered")
    with pytest.raises(InvalidRequest, match="integrity"):
        store.get(key)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "abc", "G" * 64, ""])
def test_blob_rejects_bad_keys(tmp_path, bad):
    with pytest.raises(InvalidRequest):
        FilesystemBlobStore(tmp_path).get(bad)


def test_clock():
    c = VirtualClock(parse_ts("2026-10-15T09:00:00Z"))
    assert c.advance(30) == datetime(2026, 10, 15, 9, 0, 30, tzinfo=UTC)
    assert c.advance_to(parse_ts("2026-10-15T10:00:00Z")).hour == 10
    with pytest.raises(InvalidRequest):
        c.advance(-1)
    with pytest.raises(InvalidRequest):
        c.advance_to(parse_ts("2026-10-15T09:00:00Z"))


def test_parse_and_iso():
    assert iso(parse_ts("2026-10-15T09:00:00Z")) == "2026-10-15T09:00:00Z"
    assert parse_ts("2026-10-15T14:30:00+05:30") == datetime(2026, 10, 15, 9, tzinfo=UTC)
    with pytest.raises(InvalidRequest):
        parse_ts("nope")
    with pytest.raises(InvalidRequest):
        parse_ts("2026-10-15T09:00:00")


def test_scheduler_orders_by_due_then_declaration_then_id():
    t0 = datetime(2026, 10, 15, 9, tzinfo=UTC)
    items = [
        Scheduled("c", t0 + timedelta(seconds=5), 0, "pending"),
        Scheduled("b", t0, 2, "pending"),
        Scheduled("a", t0, 2, "armed"),
        Scheduled("early", t0, 1, "pending"),
        Scheduled("fired", t0, 0, "fired"),
        Scheduled("manual", None, 0, "pending"),
        Scheduled("future", t0 + timedelta(hours=1), 0, "pending"),
    ]
    got = [i.id for i in due_in_order(items, t0 + timedelta(seconds=5))]
    assert got == ["early", "a", "b", "c"]
