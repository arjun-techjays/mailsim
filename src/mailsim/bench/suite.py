"""A fixed-fixture regression suite: scenario packs found on disk, published idempotently.

Each argument is a scenario file, a pack directory (`scenario.yaml|yml|json`, optionally
`answer_key.yaml|yml`), or a directory searched recursively for packs (sorted by path, so the
suite order is stable). Publishing an unchanged pack again reuses its stored immutable
versions; a changed pack without a version bump is refused (CONFLICT), so a suite can never
silently grade against different fixtures under the same version number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mailsim.domain.errors import InvalidRequest
from mailsim.scenario.answer_keys import create_answer_key_version, load_answer_key
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version
from mailsim.store.blob import BlobStore
from mailsim.store.repos import Scope

SCENARIO_NAMES = ("scenario.yaml", "scenario.yml", "scenario.json")
ANSWER_KEY_NAMES = ("answer_key.yaml", "answer_key.yml")


@dataclass(frozen=True)
class SuiteEntry:
    path: Path  # the scenario file
    answer_key: Path | None


@dataclass(frozen=True)
class PublishedScenario:
    path: str
    scenario_key: str
    scenario_version: int
    scenario_version_id: str
    spec_hash: str
    answer_key_version: int | None  # None: the latest compatible key (e.g. inline assertions)
    answer_key_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _pack(directory: Path) -> SuiteEntry | None:
    scenario = next((directory / n for n in SCENARIO_NAMES if (directory / n).is_file()), None)
    if scenario is None:
        return None
    return SuiteEntry(scenario, _answer_key(directory))


def _answer_key(directory: Path) -> Path | None:
    return next((directory / n for n in ANSWER_KEY_NAMES if (directory / n).is_file()), None)


def discover(paths: Iterable[Path]) -> list[SuiteEntry]:
    entries: list[SuiteEntry] = []
    for path in paths:
        if path.is_file():  # a scenario file: the answer key next to it, if any
            entries.append(SuiteEntry(path, _answer_key(path.parent)))
            continue
        if not path.is_dir():
            raise InvalidRequest(f"no such scenario file or directory: {path}")
        found = [
            pack for d in [path, *sorted(p for p in path.rglob("*") if p.is_dir())]
            if (pack := _pack(d)) is not None
        ]  # fmt: skip
        if not found:
            raise InvalidRequest(f"no scenario pack (scenario.yaml or .json) under {path}")
        entries.extend(found)
    seen: set[Path] = set()
    unique = []
    for e in entries:
        if e.path.resolve() not in seen:
            seen.add(e.path.resolve())
            unique.append(e)
    return unique


def publish(session_factory: Any, scope: Scope, blobs: BlobStore,
            entries: Sequence[SuiteEntry]) -> list[PublishedScenario]:  # fmt: skip
    """Publish every pack (scenario + answer key in one transaction each)."""
    out = []
    for entry in entries:
        loaded = load_scenario(entry.path)
        doc = load_answer_key(entry.answer_key) if entry.answer_key is not None else None
        if doc is not None and doc.answer_key.scenario != loaded.key:
            raise InvalidRequest(f"{entry.answer_key}: answer key is for "
                                 f"{doc.answer_key.scenario!r}, not {loaded.key!r}")  # fmt: skip
        with session_factory.begin() as s:
            row = create_scenario_version(s, scope, loaded, blobs)
            key = create_answer_key_version(s, scope, doc, blobs) if doc is not None else None
            out.append(PublishedScenario(
                path=str(entry.path), scenario_key=loaded.key, scenario_version=row.version,
                scenario_version_id=row.id, spec_hash=row.spec_hash,
                answer_key_version=key.version if key is not None else None,
                answer_key_hash=key.spec_hash if key is not None else None,
            ))  # fmt: skip
    return out


__all__ = ["PublishedScenario", "SuiteEntry", "discover", "publish"]
