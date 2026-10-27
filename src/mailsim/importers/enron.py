"""`sim import enron`: sample, clean, split, pseudonymize and classify Enron bodies.

Input: the LLM-PBE/enron-email parquet shards (one string column ``text``; bodies only).
Output: a JSONL corpus. Line 1 is a header (format, seed, counts, per-placeholder metadata);
every other line is one record::

    {"id": "r000001", "topic": "finance", "scores": {...}, "keywords": {...},
     "unknown_proper": 0, "ids": ["482913"],  # hashed digit runs (6+ digits) in the record
     "segments": [  # oldest first
        {"author": "p:12", "to": ["p:3"], "cc": [], "subject": "...", "kind": "reply",
         "greeting": "p:3", "signoff": "p:12", "body": "template text"}, ...]}

Bodies, subjects and parties are templates (see `pseudonymize`): no detected real name,
address or number survives. Sampling is a deterministic function of (seed, shard name, row):
the `limit` rows with the smallest keyed hash, so the same seed and inputs give a
byte-identical corpus (identifiers are hashed with a secret key, `ImportConfig.id_key`: the same
key file is needed to reproduce a corpus; see `load_key`, which never creates one). Raw text
never leaves this process except through templates; the optional registry file (real names,
for scanning generated output) and the key file must stay outside the repository (default
location under ``var/``).
"""

from __future__ import annotations

import hashlib
import heapq
import hmac
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mailsim.importers import names as N
from mailsim.importers.classify import classify
from mailsim.importers.clean import clean_body, is_garbled, split_chain
from mailsim.importers.pseudonymize import (
    PLACEHOLDER,
    Harvester,
    Pseudonymizer,
    Registry,
    external_meta,
    people_meta,
    unknown_organisations,
    unknown_proper_nouns,
)

FORMAT = "mailsim.enron-corpus/1"
MAX_SUBJECT = 120
_GREETING = re.compile(
    r"^\s*(?:(?:Hi|Hello|Dear|Hey|Good morning|Morning)[ ,]+)?\{\{([pq]:\d+):[nfl]\}\}"
    r"\s*(?:[,:!\-]|$)"
)
_SIGNOFF = re.compile(
    r"(?:^\s*|[.!?]\s+|(?:Thanks|Thank you|Regards|Best|Cheers)[,!.]?\s*)-*\s*"
    r"\{\{([pq]:\d+):[nfl]\}\}\.?\s*$"
)
# Header-block leftovers that the chain splitter did not resolve (curation rejects them).
LEFTOVER = re.compile(
    r"Forwarded by|-{3,}\s*Original Message|^\s*(?:To|cc|Cc|Subject|Sent|From)\s*:|"
    r"\bon \d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}",
    re.MULTILINE,
)


class MissingDependency(RuntimeError):
    pass


def _pyarrow() -> Any:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MissingDependency(
            "pyarrow is required to read the Enron parquet shards: "
            "uv sync --extra import   (or: pip install 'mailsim[import]')"
        ) from exc
    return pq


def read_parquet(
    paths: Iterable[Path], *, batch_size: int = 2048
) -> Iterator[tuple[str, int, str]]:
    """Yield (shard name, row index, text) for every row of the given shards."""
    pq = _pyarrow()
    for path in paths:
        pf = pq.ParquetFile(str(path))
        if "text" not in pf.schema_arrow.names:
            raise ValueError(f"{path}: expected a 'text' column, found {pf.schema_arrow.names}")
        row = 0
        for batch in pf.iter_batches(batch_size=batch_size, columns=["text"]):
            for value in batch.column(0).to_pylist():
                yield path.name, row, value or ""
                row += 1


def sample_key(seed: int, shard: str, row: int) -> str:
    return hashlib.sha256(f"{seed}:{shard}:{row}".encode()).hexdigest()


@dataclass
class ImportConfig:
    limit: int = 50_000
    seed: int = 7
    strip_quotes: bool = True
    min_chars: int = 40
    max_chars: int = 20_000
    harvest_all: bool = True  # build the name registry from every row, not only the sample
    id_key: bytes = b""  # secret key for hashed identifiers: required (the CLI reads id.key)


@dataclass
class ImportStats:
    rows_read: int = 0
    sampled: int = 0
    dropped_size: int = 0
    dropped_garbled: int = 0
    records: int = 0
    topics: dict[str, int] = field(default_factory=dict)
    people: int = 0


def select(rows: Iterable[tuple[str, int, str]], cfg: ImportConfig, harvester: Harvester,
           stats: ImportStats) -> list[tuple[str, str, int, str]]:  # fmt: skip
    """Keyed-hash sample of `cfg.limit` rows (sorted by key); feeds the harvester. Rows outside
    the size bounds are skipped before sampling, so the sample holds `limit` usable rows."""
    heap: list[tuple[str, str, int, str]] = []  # max-heap via negated ordering
    for shard, row, text in rows:
        stats.rows_read += 1
        if cfg.harvest_all:
            harvester.feed(text)
        if not cfg.min_chars <= len(text) <= cfg.max_chars:
            stats.dropped_size += 1
            continue
        key = sample_key(cfg.seed, shard, row)
        item = (_neg(key), shard, row, text)
        if len(heap) < cfg.limit:
            heapq.heappush(heap, item)
        elif item[0] > heap[0][0]:
            heapq.heapreplace(heap, item)
    chosen = sorted((_neg(k), shard, row, text) for k, shard, row, text in heap)
    if not cfg.harvest_all:
        for *_, text in chosen:
            harvester.feed(text)
    stats.sampled = len(chosen)
    return chosen


def _neg(key: str) -> str:
    """Order-reversing bijection on lower-case hex strings (for a max-heap of keys)."""
    return key.translate(str.maketrans("0123456789abcdef", "fedcba9876543210"))


def _subject(text: str | None) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    return s[:MAX_SUBJECT]


def _marker(lines: list[str], rx: re.Pattern[str]) -> str | None:
    for line in lines:
        m = rx.search(line)
        if m:
            return m.group(1)
    return None


def greeting_ref(body: str) -> str | None:
    lines = [ln for ln in body.split("\n") if ln.strip()][:2]
    return _marker(lines, _GREETING)


def signoff_ref(body: str) -> str | None:
    lines = [ln for ln in body.split("\n") if ln.strip()][-3:]
    return _marker(list(reversed(lines)), _SIGNOFF)


def build_record(raw: str, pseudo: Pseudonymizer, cfg: ImportConfig, stats: ImportStats
                 ) -> dict[str, Any] | None:  # fmt: skip
    if not cfg.min_chars <= len(raw) <= cfg.max_chars:
        stats.dropped_size += 1
        return None
    segments = split_chain(raw.replace("\r\n", "\n"))
    local: set[int] = set()
    pseudo.emitted = set()
    out: list[dict[str, Any]] = []
    # header fields first: the people they name resolve first-name mentions in every body
    for seg in segments:
        h = seg.headers
        entry: dict[str, Any] = {"author": None, "to": [], "cc": [], "subject": "", "kind": None}
        if h is not None:
            authors = pseudo.parties(h.from_ or "", local)
            entry.update(author=authors[0] if authors else None, to=pseudo.parties(h.to, local),
                         cc=pseudo.parties(h.cc, local), kind=h.kind,
                         subject=_subject(pseudo.apply(_subject(h.subject), local).text))  # fmt: skip
            if h.forwarded_by:
                fwd = pseudo.parties(h.forwarded_by, local)
                entry["forwarded_by"] = fwd[0] if fwd else None
        out.append(entry)
    for seg, entry in zip(segments, out, strict=True):
        body = clean_body(seg.body, strip_quotes=cfg.strip_quotes)
        template = pseudo.apply(body, local).text if body else ""
        entry.update(body=template, greeting=greeting_ref(template), signoff=signoff_ref(template))
    out.reverse()  # oldest first
    bodies = [s["body"] for s in out if s["body"]]
    if not bodies or is_garbled(PLACEHOLDER.sub("Name", "\n".join(bodies))):
        stats.dropped_garbled += 1
        return None
    text = "\n".join([s["subject"] for s in out] + bodies)
    result = classify(PLACEHOLDER.sub(" ", text))
    return {
        "topic": result.topic,
        "scores": result.scores,
        "keywords": result.keywords,
        "unknown_proper": len(unknown_proper_nouns(text, pseudo.reg)),
        "unknown_orgs": len(unknown_organisations(text, pseudo.reg)),
        "leftover_headers": any(LEFTOVER.search(b) for b in bodies),
        "ids": sorted(pseudo.emitted),
        "segments": out,
    }


def import_enron(
    paths: list[Path], cfg: ImportConfig
) -> tuple[list[dict[str, Any]], dict[str, Any], Registry]:
    """Run the import; returns (records, header, registry). Pure function of inputs + cfg."""
    stats = ImportStats()
    harvester = Harvester()
    chosen = select(read_parquet(paths), cfg, harvester, stats)
    return build_corpus(chosen, harvester.finish(), cfg, stats)


def build_corpus(chosen: list[tuple[str, str, int, str]], registry: Registry, cfg: ImportConfig,
                 stats: ImportStats) -> tuple[list[dict[str, Any]], dict[str, Any], Registry]:  # fmt: skip
    """Records, header and registry from the sampled rows (the second half of the import)."""
    pseudo = Pseudonymizer(registry, cfg.seed, cfg.id_key)
    records: list[dict[str, Any]] = []
    for _key, _shard, _row, text in chosen:
        rec = build_record(text, pseudo, cfg, stats)
        if rec is None:
            continue
        rec = {"id": f"r{len(records) + 1:06d}", **rec}
        records.append(rec)
        stats.topics[rec["topic"]] = stats.topics.get(rec["topic"], 0) + 1
    pids: set[int] = set()
    xids: set[int] = set()
    for rec in records:
        blob = json.dumps(rec["segments"])
        for kind, num, _ in PLACEHOLDER.findall(blob):
            (pids if kind == "p" else xids if kind == "x" else set()).add(int(num))
        for ref in re.findall(r'"([px]):(\d+)"', blob):
            (pids if ref[0] == "p" else xids).add(int(ref[1]))
    stats.records = len(records)
    stats.people = len(registry.people)
    header = {
        "format": FORMAT,
        "seed": cfg.seed,
        "limit": cfg.limit,
        "strip_quotes": cfg.strip_quotes,
        "stats": {k: v for k, v in sorted(vars(stats).items())},
        "people": {str(k): v for k, v in people_meta(registry, cfg.seed, pids).items()},
        "externals": {str(k): v for k, v in external_meta(pseudo, xids).items()},
    }
    return records, header, registry


def write_corpus(path: Path, records: list[dict[str, Any]], header: dict[str, Any]) -> str:
    """Write the JSONL corpus; returns its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(header, sort_keys=True, ensure_ascii=False)]
    lines += [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records]
    data = ("\n".join(lines) + "\n").encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def read_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: empty corpus")
    header = json.loads(lines[0])
    if header.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} file")
    return header, [json.loads(line) for line in lines[1:] if line.strip()]


KEY_DOC = "docs/leadership-inboxes.md, section 'Identifier key'"
_KEY_CHECK_MSG = b"mailsim id-key check v1"


def id_key_check(key: bytes) -> str:
    """A public check value for the id key: a truncated HMAC of a constant. It reveals nothing
    usable about the key but tells the team key from any other (`names.ID_KEY_CHECK`)."""
    return hmac.new(key, _KEY_CHECK_MSG, hashlib.sha256).hexdigest()[:16]


def load_key(path: Path, *, expected: str | None = None) -> bytes:
    """The secret key for hashed identifiers and the deny list, read from `path` and checked
    against `expected` (default `names.ID_KEY_CHECK`). Never created here: a new key would
    silently change every hashed id and disable the deny list, so a missing or different key
    is an error. The team key lives in the team password manager (see `KEY_DOC`)."""
    if not path.is_file():
        raise ValueError(
            f"{path}: id key not found. It is the team's shared secret: restore it from the team "
            f"password manager ({KEY_DOC}). Do not create a new one: hashed ids and the deny "
            "list would no longer match the committed pack."
        )
    text = path.read_text(encoding="ascii").strip()
    if len(text) < 32:
        raise ValueError(f"{path}: id key too short (expected 64 hex characters)")
    key = text.encode()
    if id_key_check(key) != (N.ID_KEY_CHECK if expected is None else expected):
        raise ValueError(
            f"{path}: this is not the team id key (its check differs from names.ID_KEY_CHECK). "
            f"Restore the team key ({KEY_DOC}); rotating it is a manual procedure."
        )
    return key


def key_is_private(path: Path) -> bool:
    """True when only the owner can read the key file."""
    return path.stat().st_mode & 0o077 == 0


def write_registry(path: Path, registry: Registry) -> None:
    """Real names harvested by the import (for `sim import scan --names`). Keep it out of git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"names": registry.real_names()}, indent=0) + "\n", encoding="utf-8")
