"""Helpers to materialise scenario variants on disk for tests."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "scenarios" / "finance.invoice-bank-change"
EXAMPLE = EXAMPLE_DIR / "scenario.yaml"


def example_dict() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE.read_text())


def write_variant(tmp_path: Path, mutate=None, name: str = "scenario.yaml") -> Path:
    """Copy the example scenario + fixtures into tmp_path, applying `mutate(dict)`."""
    dest = tmp_path / "scn"
    if not dest.exists():
        shutil.copytree(EXAMPLE_DIR, dest)
    data = copy.deepcopy(example_dict())
    if mutate:
        mutate(data["scenario"])
    path = dest / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path
