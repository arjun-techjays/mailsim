"""`sim import enron` / `sim import scan` / `sim generate leadership` end to end on a synthetic
parquet fixture: determinism, sampling, PII handling, error paths."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from mailsim.cli import main
from mailsim.importers import enron
from mailsim.importers.pseudonymize import PLACEHOLDER, Renderer
from tests.importers.conftest import PEOPLE, make_texts


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, json.loads(out or err)


def test_import_is_deterministic_and_pseudonymized(shards, tmp_path):
    cfg = enron.ImportConfig(limit=1000, seed=7)
    recs, header, registry = enron.import_enron(shards, cfg)
    first = enron.write_corpus(tmp_path / "a.jsonl", recs, header)
    again = enron.write_corpus(tmp_path / "b.jsonl", *enron.import_enron(shards, cfg)[:2])
    assert first == again
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    stats = header["stats"]
    assert stats["rows_read"] == len(make_texts())
    assert stats["sampled"] == stats["rows_read"] - stats["dropped_size"]
    assert stats["dropped_size"] == 1 and stats["dropped_garbled"] == 1
    assert stats["records"] == len(recs) and set(stats["topics"]) >= {"finance", "board", "hr"}
    raw = (tmp_path / "a.jsonl").read_text().split("\n", 1)[1]  # records (header: metadata)
    for _first, last in PEOPLE:
        assert last not in raw  # surnames never survive into templates
    assert "enron" not in raw.lower() and "713" not in raw and "/HOU/" not in raw
    threads = [r for r in recs if len(r["segments"]) > 1]
    assert threads and all(s.get("author") for r in threads for s in r["segments"][:-1])
    renderer = Renderer(7, {int(k): v for k, v in header["people"].items()},
                        {int(k): v for k, v in header["externals"].items()})  # fmt: skip
    text = "\n".join(renderer.render(s["body"]) for r in recs for s in r["segments"])
    assert not PLACEHOLDER.search(text) and "@corvane.example.test" in text
    header2, recs2 = enron.read_corpus(tmp_path / "a.jsonl")
    assert recs2 == json.loads(json.dumps(recs)) and header2["format"] == enron.FORMAT
    enron.write_registry(tmp_path / "reg.json", registry)
    names = json.loads((tmp_path / "reg.json").read_text())["names"]
    assert "Marta Pellowe" in names


def test_sampling_is_keyed_by_seed(shards):
    a, _, _ = enron.import_enron(shards, enron.ImportConfig(limit=40, seed=1))
    b, _, _ = enron.import_enron(shards, enron.ImportConfig(limit=40, seed=1, harvest_all=False))
    c, _, _ = enron.import_enron(shards, enron.ImportConfig(limit=40, seed=2))
    assert len(a) <= 40 and [r["topic"] for r in a] == [r["topic"] for r in b]
    assert [r["segments"] for r in a] != [r["segments"] for r in c]
    assert enron.sample_key(1, "0.parquet", 3) != enron.sample_key(2, "0.parquet", 3)


def test_corpus_and_parquet_errors(tmp_path):
    (tmp_path / "empty.jsonl").write_text("")
    with pytest.raises(ValueError, match="empty"):
        enron.read_corpus(tmp_path / "empty.jsonl")
    (tmp_path / "other.jsonl").write_text('{"format": "x"}\n')
    with pytest.raises(ValueError, match="not a"):
        enron.read_corpus(tmp_path / "other.jsonl")
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"body": ["x"]}), str(tmp_path / "bad.parquet"))
    with pytest.raises(ValueError, match="'text' column"):
        list(enron.read_parquet([tmp_path / "bad.parquet"]))


def test_cli_import_scan_generate(shards, tmp_path, capsys):
    corpus, reg = tmp_path / "var" / "corpus.jsonl", tmp_path / "var" / "registry.json"
    code, out = _run(capsys, "import", "enron", "--parquet", *map(str, shards), "--limit", "1000",
                     "--seed", "7", "--out", str(corpus), "--registry-out", str(reg))  # fmt: skip
    assert code == 0 and out["ok"] and out["records"] > 200 and len(out["sha256"]) == 64
    code, out = _run(capsys, "import", "enron", "--parquet", *map(str, shards), "--limit", "50",
                     "--out", str(tmp_path / "q.jsonl"), "--keep-quotes", "--sample-harvest")  # fmt: skip
    assert code == 0 and out["sampled"] == 50
    pack = tmp_path / "pack"
    code, out = _run(capsys, "generate", "leadership", "--corpus", str(corpus), "--out", str(pack),
                     "--roles", "ceo,cfo", "--volume", "ceo=8, cfo=7", "--names", str(reg),
                     "--seed", "3", "--days", "6", "--start-at", "2026-10-16T08:00:00Z")  # fmt: skip
    assert code == 0, out
    assert [s["messages"] for s in out["scenarios"]] == [8, 7]
    assert (pack / "ceo" / "scenario.yaml").is_file() and (pack / "cfo" / "pack.json").is_file()
    code, out = _run(capsys, "import", "scan", str(pack), "--names", str(reg))
    assert (code, out["ok"], out["findings"]) == (0, True, 0)
    leak = tmp_path / "leak.txt"
    leak.write_text("Forwarded by Marta Pellowe/HOU/ECT\ncall 713-853-1234\n")
    code, out = _run(capsys, "import", "scan", str(leak), "--names", str(reg))
    assert code == 1 and out["by_kind"] == {
        "enron-acronym": 1,
        "lotus-notes": 1,
        "phone": 1,
        "real-name": 1,
    }
    # generate again: fixtures are replaced, output identical
    before = {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()}
    _run(capsys, "generate", "leadership", "--corpus", str(corpus), "--out", str(pack), "--roles",
         "ceo,cfo", "--volume", "ceo=8,cfo=7", "--names", str(reg), "--seed", "3", "--days",
         "6")  # fmt: skip
    assert {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()} == before


def test_cli_errors(tmp_path, capsys, monkeypatch, shards):
    code, out = _run(capsys, "generate", "leadership", "--corpus", str(tmp_path / "missing.jsonl"),
                     "--names", str(tmp_path / "missing.json"))  # fmt: skip
    assert code == 2 and out["code"] == "INVALID_REQUEST"
    for argv in (["generate", "leadership", "--corpus", "c.jsonl"],  # --names or --no-names
                 ["generate", "leadership", "--corpus", "c", "--no-names", "--days", "1"],
                 ["import", "enron", "--parquet", "x.parquet", "--limit", "0"]):  # fmt: skip
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    capsys.readouterr()
    real_import = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name.startswith("pyarrow"):
            raise ImportError("no pyarrow")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    code, out = _run(capsys, "import", "enron", "--parquet", str(shards[0]), "--out",
                     str(tmp_path / "x.jsonl"))  # fmt: skip
    assert code == 2 and out["code"] == "MISSING_DEPENDENCY" and "--extra import" in out["message"]
    monkeypatch.setattr(builtins, "__import__", real_import)
    with pytest.raises(FileNotFoundError):
        enron.read_corpus(Path(tmp_path / "nope"))


def test_cli_generate_without_registry_warns(shards, tmp_path, capsys):
    corpus = tmp_path / "corpus.jsonl"
    enron.write_corpus(corpus, *enron.import_enron(shards, enron.ImportConfig(limit=1000))[:2])
    code, out = _run(capsys, "generate", "leadership", "--corpus", str(corpus), "--out",
                     str(tmp_path / "p"), "--roles", "coo", "--volume", "coo=3", "--no-names")  # fmt: skip
    assert code == 0 and out["scenarios"][0]["messages"] == 3


def test_size_filter_applies_before_sampling(shards):
    _, header, _ = enron.import_enron(shards, enron.ImportConfig(limit=5, seed=1, min_chars=40))
    assert header["stats"]["sampled"] == 5 and header["stats"]["dropped_size"] == 1


def test_cli_still_raises_unexpected_errors_for_engine_commands(monkeypatch):
    def boom(_path):
        raise ValueError("engine bug")

    monkeypatch.setattr("mailsim.cli.load_scenario", boom)
    with pytest.raises(ValueError, match="engine bug"):
        main(["validate", "x.yaml"])
