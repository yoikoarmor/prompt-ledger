import concurrent.futures
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FakeEmbedder, payload

from prompt_ledger.config import Config, LedgerError, contained
from prompt_ledger.core import Ledger
from prompt_ledger.embedding import normalize


def test_boundary_same_batch_and_exact_idempotence(ledger, fake):
    result = ledger.ingest(
        [("one.json", payload("base")), ("two.json", payload("boundary")), ("three.json", payload("below"))]
    )["results"]
    assert [r["status"] for r in result] == ["accepted", "duplicate", "accepted"]
    assert result[1]["positive_score"] == 0.95
    calls = fake.calls
    again = ledger.ingest([("renamed.json", payload("base"))])["results"][0]
    assert again["reused"] and fake.calls == calls
    assert len(ledger.export()["records"]) == 2


def test_negative_difference_empty_rules(ledger):
    result = ledger.ingest(
        [
            ("a.json", payload("base", "negative-a")),
            ("b.json", payload("base", "negative-b")),
            ("c.json", payload("base", "")),
            ("d.json", payload("boundary", "")),
        ]
    )["results"]
    assert [r["status"] for r in result] == ["accepted", "accepted", "accepted", "duplicate"]
    assert result[1]["negative_score"] == -1
    assert result[2]["negative_score"] == 0
    assert result[3]["negative_score"] == 1


def test_model_settings_mismatch(cfg, ledger):
    ledger.ingest([("a.json", payload("base"))])
    other = FakeEmbedder()
    other.identity = {**other.identity, "revision": "v2"}
    with pytest.raises(LedgerError, match="differ"):
        Ledger(cfg, other).ingest([("b.json", payload("base"))])
    changed = Config(cfg.root, threshold=0.9)
    with pytest.raises(LedgerError, match="differ"):
        Ledger(changed, FakeEmbedder()).ingest([("b.json", payload("base"))])


@pytest.mark.parametrize("vector", [[0, 0], [math.nan, 1], [math.inf, 1], [1]])
def test_invalid_vectors(vector):
    with pytest.raises(LedgerError):
        normalize(vector, 2)


def test_dry_run_and_review(ledger):
    result = ledger.ingest([("a.json", payload("base")), ("bad.json", b"{broken")], dry_run=True)
    assert [r["status"] for r in result["results"]] == ["accepted", "review"]
    assert ledger.status()["counts"] == {"accepted": 0, "duplicate": 0, "review": 0}
    assert ledger.status()["space"] is None
    record = ledger.ingest([("bad.json", b"{broken")])["results"][0]
    ledger.resolve(record["id"], "base", "")
    assert ledger.status()["pending_review"] == 0
    assert len(ledger.export()["records"]) == 1


def test_batch_rollback_on_embedding_failure(ledger, fake):
    encode = fake.encode

    def broken(text):
        if text == "fail":
            raise LedgerError("embedding", "synthetic failure")
        return encode(text)

    fake.encode = broken
    with pytest.raises(LedgerError):
        ledger.ingest([("a.json", payload("base")), ("b.json", payload("fail"))])
    assert ledger.status()["counts"]["accepted"] == 0
    assert ledger.status()["space"] is None


def test_concurrent_import(cfg):
    ledgers = [Ledger(cfg, FakeEmbedder()) for _ in range(4)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: item.ingest([("a.json", payload("base"))]), ledgers))
    assert sum(bool(r["results"][0].get("reused")) for r in results) == 3
    assert ledgers[0].status()["counts"]["accepted"] == 1


def test_atomic_export(cfg, ledger, monkeypatch):
    ledger.ingest([("a.json", payload("base"))])
    result = ledger.export_file("accepted.json")
    target = cfg.root / result["path"]
    original = target.read_bytes()

    def denied(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(OSError):
        ledger.export_file("accepted.json")
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".export-*"))


@pytest.mark.parametrize(
    "path", ["../secret", "/secret", "C:/secret", "foo\\bar", "foo:stream", "./a", "a/../b"]
)
def test_traversal(cfg, path):
    with pytest.raises(LedgerError):
        contained(cfg.root, path)


def test_symlink(cfg, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = cfg.root / "link"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if result.returncode:
            pytest.skip("Windows junction creation unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LedgerError):
        contained(cfg.root, "link/file.json")


def test_different_cwd_cli_and_privacy(cfg, tmp_path):
    cfg.path("input_dir").mkdir()
    (cfg.path("input_dir") / "sample.json").write_bytes(payload("synthetic harmless flower"))
    package_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "prompt_ledger.cli",
            "--root",
            str(cfg.root),
            "import",
            "sample.json",
            "--check-only",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["results"] == [{"status": "parsed"}]
    assert "flower" not in result.stdout and not cfg.path("data_dir").exists()


def test_privacy_and_relative_export(ledger):
    ledger.ingest([("日本語/file.json", payload("base"))])
    row = ledger.records()["records"][0]
    assert "source" not in row and "positive" not in row
    assert ledger.export()["records"][0]["source"] == "日本語/file.json"


def test_cli_runtime_failure_is_json(monkeypatch, capsys):
    import sqlite3

    from prompt_ledger import cli

    def broken(args):
        raise sqlite3.OperationalError("synthetic private diagnostic")

    monkeypatch.setattr(cli, "execute", broken)
    assert cli.main(["status"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "runtime"
    assert "private" not in output.out


def test_resolved_review_link_cannot_be_rewritten(ledger):
    original = ledger.ingest([("review.json", b"{}")])["results"][0]["id"]
    first = ledger.resolve(original, "base", "")["result"]["id"]
    before = ledger.status()["counts"]
    with pytest.raises(LedgerError) as error:
        ledger.resolve(original, "opposite", "")
    assert error.value.code == "already_resolved"
    assert ledger.status()["counts"] == before
    rows = ledger.records()["records"]
    assert next(row for row in rows if row["id"] == original)["resolved_by"] == first


def test_metadata_only_import_does_not_require_or_pin_model(cfg):
    ledger = Ledger(cfg)  # No manifest or model; malformed metadata needs only review.
    assert ledger.ingest([("review.json", b"{}")])["results"][0]["status"] == "review"
    assert ledger.status()["space"] is None


def test_dependency_change_after_review_does_not_poison_space(cfg):
    fake = FakeEmbedder()
    fake.identity = {**fake.identity, "runtime_versions": {"torch": "not-installed"}}
    ledger = Ledger(cfg, fake)
    ledger.ingest([("review.json", b"{}")])
    assert ledger.status()["space"] is None
    fake.identity = {**fake.identity, "runtime_versions": {"torch": "synthetic-installed"}}
    ledger.ingest([("accepted.json", payload("base"))])
    assert ledger.status()["space"]["runtime_versions"]["torch"] == "synthetic-installed"
    fake.identity = {**fake.identity, "revision": "different-space"}
    with pytest.raises(LedgerError, match="differ"):
        ledger.ingest([("another.json", payload("opposite"))])


def test_legacy_metadata_only_pin_is_repaired(cfg):
    ledger = Ledger(cfg, FakeEmbedder())
    ledger.ingest([("review.json", b"{}")])
    with ledger.connect() as conn:
        conn.execute(
            "INSERT INTO meta VALUES ('space',?)", ('{"runtime_versions":{"torch":"not-installed"}}',)
        )
    ledger.ingest([("accepted.json", payload("base"))])
    assert ledger.status()["space"]["model_id"] == "synthetic-test-only"


def test_negative_token_failure_does_not_save_partial_vector_or_pin(ledger, fake):
    original_encode = fake.encode

    def limited(text):
        if text == "too long":
            raise LedgerError("token_limit", "synthetic token limit")
        return original_encode(text)

    fake.encode = limited
    result = ledger.ingest([("review.json", payload("base", "too long"))])
    assert result["results"][0]["status"] == "review"
    assert ledger.status()["space"] is None
    with ledger.connect() as conn:
        row = conn.execute("SELECT positive_vector,negative_vector FROM records").fetchone()
        assert row["positive_vector"] is None and row["negative_vector"] is None
    fake.identity = {**fake.identity, "revision": "v2"}
    ledger.ingest([("accepted.json", payload("base"))])
    assert ledger.status()["space"]["revision"] == "v2"


def test_cli_preserves_explicit_order_and_batch_winner(cfg, ledger):
    from prompt_ledger.cli import read_inputs

    root = cfg.path("input_dir")
    root.mkdir()
    (root / "b.json").write_bytes(payload("base"))
    (root / "a.json").write_bytes(payload("boundary"))
    files = read_inputs(cfg, ["b.json", "a.json"], False)
    assert [name for name, _ in files] == ["b.json", "a.json"]
    result = ledger.ingest(files)["results"]
    assert [row["status"] for row in result] == ["accepted", "duplicate"]
    assert ledger.export()["records"][0]["source"] == "b.json"


def test_cli_sorts_each_directory_block_without_reordering_arguments(cfg):
    from prompt_ledger.cli import read_inputs

    root = cfg.path("input_dir")
    (root / "second" / "nested").mkdir(parents=True)
    (root / "first").mkdir()
    paths = [
        "z.json",
        "second/z.json",
        "second/a.json",
        "second/nested/b.json",
        "a.json",
        "first/z.json",
        "first/a.json",
    ]
    for path in paths:
        (root / path).write_bytes(payload("base"))
    files = read_inputs(cfg, ["z.json", "second", "a.json", "first"], True)
    assert [name for name, _ in files] == [
        "z.json",
        "second/a.json",
        "second/nested/b.json",
        "second/z.json",
        "a.json",
        "first/a.json",
        "first/z.json",
    ]
