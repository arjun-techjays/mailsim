"""Deterministic, prefixed identifiers.

Simulation paths draw ids from a seeded generator so the same seed + the same command
sequence produce byte-identical ids. Real randomness is only used for tenant/run ids
(control-plane objects), never inside a run's mailbox state.
"""

from __future__ import annotations

import random
import re
import uuid

_PREFIX_RE = re.compile(r"^[a-z]{2,8}_[0-9a-z]+$")


def new_control_id(prefix: str) -> str:
    """Non-deterministic id for control-plane objects (tenant, project, run)."""
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def is_valid_id(value: str) -> bool:
    return bool(_PREFIX_RE.match(value))


class IdGenerator:
    """Seeded id generator. State is (seed, counter) so it can be resumed exactly."""

    def __init__(self, seed: int, counter: int = 0) -> None:
        self.seed = seed
        self.counter = counter

    def next(self, prefix: str) -> str:
        # Derive each id from (seed, counter) independently: resumable without hidden state.
        rng = random.Random(f"{self.seed}:{self.counter}")  # noqa: S311 - determinism, not security
        self.counter += 1
        return f"{prefix}_{rng.getrandbits(64):016x}"

    def message_id_header(self, domain: str) -> str:
        rng = random.Random(f"{self.seed}:{self.counter}:mid")  # noqa: S311
        self.counter += 1
        return f"<{rng.getrandbits(80):020x}@{domain}>"
