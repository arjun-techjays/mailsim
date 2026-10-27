"""Issue #6: the PostgreSQL session timezone must never reach MIME bytes or the state hash.

psycopg returns `timestamptz` values in the session's TimeZone, so a run's virtual time read
back from a database set to Asia/Kolkata carries +05:30. The engine must still stamp every
Date header in UTC, and the same command sequence must hash identically in either zone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from mailsim.domain.clock import parse_ts
from mailsim.engine.core import SimEngine
from mailsim.engine.facades import Controller, EvaluatorView
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store import repos
from mailsim.store.blob import FilesystemBlobStore
from mailsim.store.db import make_engine, run_migrations
from tests.conftest import ADMIN_URL, TEST_DB, scratch_database
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
GEN = {"mode": "scripted", "rule_index": 0}


@pytest.fixture(autouse=True)
def _database_default_zones(monkeypatch):
    """libpq sends PGTZ at connect time, overriding the per-database defaults set below."""
    monkeypatch.delenv("PGTZ", raising=False)


@contextmanager
def zoned_database(suffix: str, zone: str) -> Iterator[str]:
    """A migrated scratch database whose sessions default to `zone`."""
    assert re.fullmatch(r"[A-Za-z_/+-]+", zone), zone  # inlined below (DDL takes no binds)
    with scratch_database(suffix) as url:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"ALTER DATABASE \"{TEST_DB}_{suffix}\" SET timezone TO '{zone}'"))
        admin.dispose()
        run_migrations(url)
        yield url


def _date_header(raw: bytes) -> str:
    value = message_from_bytes(raw)["Date"]
    assert value is not None
    return str(value)


def _play(url: str, scenario: Path, blobs_dir: Path) -> dict[str, Any]:
    """The same command sequence on an engine WITHOUT the UTC pin (plain create_engine)."""
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            zone = conn.execute(text("SHOW TimeZone")).scalar_one()
        sf = sessionmaker(engine, expire_on_commit=False)
        with sf.begin() as s:
            t = repos.create_tenant(s, "t")
            p = repos.create_project(s, t.id, "p")
        scope = repos.Scope(t.id, p.id)
        blobs = FilesystemBlobStore(blobs_dir)
        with sf.begin() as s:
            version = create_scenario_version(s, scope, load_scenario(scenario), blobs).id
        ctl = Controller(SimEngine(sf, blobs), scope)
        ev = EvaluatorView(SimEngine(sf, blobs), scope)
        run = ctl.create_run(version)
        ctl.start_run(run)
        fm = ctl.mailbox_id_for(run, "finance_manager")
        ctl.inject(run, fm, {"from": "vendor@example.test", "to": ["finance@example.test"],
                             "subject": "Remittance", "text": "Sent today."})  # fmt: skip
        ctl.advance(run, 3600)
        with_mailbox = ctl.persona_reply(run, "vendor", to=["finance@example.test"],
                                         subject="Re: Remittance", text="Confirmed.",
                                         generation=GEN)  # fmt: skip
        without_mailbox = ctl.persona_reply(run, "cfo", to=["finance@example.test"],
                                            subject="Approval", text="Approved.",
                                            generation=GEN)  # fmt: skip
        state = ev.state(run)
        rows = {m["id"]: m for m in state["messages"]}
        delivered = [
            rows[res["delivered_message_ids"][0]] for res in (with_mailbox, without_mailbox)
        ]
        return {
            "zone": zone,
            "hash": ev.state_hash(run),
            "virtual_time": parse_ts(ctl.run_info(run)["virtual_time"]),
            "dates": [_date_header(blobs.get(m["raw_blob_ref"])) for m in delivered],
            "replay_matches": ctl.replay_run(run).matches,
        }
    finally:
        engine.dispose()


def test_state_hash_and_date_headers_do_not_depend_on_the_session_timezone(tmp_path):
    def tweak(s):
        s["personas"].append({"id": "cfo", "email": "cfo@example.test", "role": "CFO"})

    scenario = write_variant(tmp_path, tweak)
    with zoned_database("tz_utc", "UTC") as utc_url:
        utc = _play(utc_url, scenario, tmp_path / "blobs_utc")
    with zoned_database("tz_ist", "Asia/Kolkata") as ist_url:
        ist = _play(ist_url, scenario, tmp_path / "blobs_ist")

    assert (utc["zone"], ist["zone"]) == ("UTC", "Asia/Kolkata"), "the zones are really set"
    for run in (utc, ist):
        for header in run["dates"]:
            assert header.endswith("+0000"), header
            assert parsedate_to_datetime(header) == run["virtual_time"]
        assert run["replay_matches"]
    assert ist["dates"] == utc["dates"]
    assert ist["hash"] == utc["hash"]


def test_make_engine_pins_every_connection_to_utc():
    with zoned_database("tz_pin", "Asia/Kolkata") as url:
        raw = create_engine(url)
        with raw.connect() as conn:
            assert conn.execute(text("SHOW TimeZone")).scalar_one() == "Asia/Kolkata"
        raw.dispose()

        engine = make_engine(url)
        try:
            with engine.connect() as a, engine.connect() as b:  # two pooled connections
                for conn in (a, b):
                    assert conn.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
                    assert conn.execute(text("SELECT now()::text")).scalar_one().endswith("+00")
            with engine.connect() as again:  # a connection handed back by the pool
                assert again.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
        finally:
            engine.dispose()
