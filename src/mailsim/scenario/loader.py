"""Load scenario files (YAML authoring / JSON canonical) safely."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mailsim.domain.errors import InvalidRequest
from mailsim.scenario.schema import ScenarioDoc, canonical_dict, spec_hash
from mailsim.scenario.validator import ValidationReport, resolve_fixture, validate_scenario

MAX_SCENARIO_BYTES = 1024 * 1024


@dataclass
class LoadedScenario:
    doc: ScenarioDoc
    spec: dict[str, Any]
    spec_hash: str
    source: bytes
    base_dir: Path
    fixtures: dict[str, bytes]  # relative path -> content
    report: ValidationReport

    @property
    def key(self) -> str:
        return self.doc.scenario.id

    @property
    def version(self) -> int:
        return self.doc.scenario.version


def parse_source(source: bytes, *, is_json: bool = False) -> object:
    if len(source) > MAX_SCENARIO_BYTES:
        raise InvalidRequest("scenario file too large")
    try:
        if is_json:
            return json.loads(source)
        return yaml.safe_load(source)  # safe_load only: never construct arbitrary objects
    except (yaml.YAMLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidRequest(
            f"scenario is not valid {'JSON' if is_json else 'YAML'}: {exc}"
        ) from exc


def load_scenario(path: Path) -> LoadedScenario:
    """Read, validate and gather fixtures. Raises InvalidRequest listing every problem."""
    path = Path(path)
    if not path.is_file():
        raise InvalidRequest(f"scenario file not found: {path}")
    source = path.read_bytes()
    data = parse_source(source, is_json=path.suffix.lower() == ".json")
    report = validate_scenario(data, path.parent)
    report.raise_if_invalid()
    assert report.doc is not None
    fixtures: dict[str, bytes] = {}
    for mb in report.doc.scenario.mailboxes:
        for msg in mb.initial_messages:
            fixtures[msg.fixture] = resolve_fixture(path.parent, msg.fixture).read_bytes()
    spec = canonical_dict(report.doc)
    return LoadedScenario(report.doc, spec, spec_hash(spec), source, path.parent, fixtures, report)


def fixture_manifest(fixtures: dict[str, bytes]) -> dict[str, str]:
    return {p: hashlib.sha256(b).hexdigest() for p, b in sorted(fixtures.items())}
