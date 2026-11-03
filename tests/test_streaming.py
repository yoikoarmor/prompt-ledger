import hashlib
import os
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.config import MAX_METADATA
from prompt_ledger.inputs import read_inputs
from prompt_ledger.parsers import extract
from prompt_ledger.streaming import MetadataStream
from prompt_ledger.web import create_app


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def large_png_chunks():
    yield b"\x89PNG\r\n\x1a\n"
    yield chunk(b"tEXt", b"parameters\0base\nSteps: 20, Seed: 1")
    yield struct.pack(">I4s", 32 * 1024 * 1024, b"IDAT")
    for _ in range(512):
        yield b"x" * 65536  # synthetic compressed-pixel placeholder, never rendered
    yield b"\0" * 4
    yield chunk(b"IEND", b"")


def test_large_image_sieve_bounded_memory():
    stream = MetadataStream("日本語/large.png")
    full_hash = hashlib.sha256()
    for part in large_png_chunks():
        stream.feed(part)
        full_hash.update(part)
        assert len(stream.output) + len(stream.part) < 2048
    metadata = stream.finish()
    assert metadata.fingerprint == full_hash.digest()
    assert extract("large.png", metadata).positive == "base"


def test_chunk_boundaries_and_truncation():
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"tEXt", b"parameters\0base\nSteps: 1,") + chunk(b"IEND", b"")
    stream = MetadataStream("x.png")
    for byte in raw:
        stream.feed(bytes([byte]))
    assert extract("x.png", stream.finish()).positive == "base"
    truncated = MetadataStream("x.png")
    truncated.feed(raw[:-1])
    assert truncated.finish().error == "format"
    huge_json = MetadataStream("x.json")
    huge_json.feed(b" " * (MAX_METADATA + 1))
    assert huge_json.finish().error == "metadata_limit"
    assert len(huge_json.output) == 0


def test_folder_unlimited_sequential_and_io_errors(cfg, ledger):
    root = cfg.path("input_dir") / "日本語 folder"
    root.mkdir(parents=True)
    for i in range(250):
        (root / f"{i:04}.json").write_bytes(payload("base"))
    result = ledger.ingest(read_inputs(cfg, ["日本語 folder"], True))
    assert result["processed"] == 250 and result["results_truncated"]
    assert len(result["results"]) == 200
    with patch.object(Path, "open", side_effect=PermissionError("synthetic")):
        failed = list(read_inputs(cfg, ["日本語 folder/0000.json"], True))
    assert failed[0][1].error == "input_io"


def test_folder_link_cycle_reported_without_following(cfg):
    root = cfg.path("input_dir") / "folder"
    root.mkdir(parents=True)
    link = root / "cycle"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(root)], check=False, capture_output=True
        )
        if result.returncode:
            pytest.skip("Junction creation unavailable")
    else:
        link.symlink_to(root, target_is_directory=True)
    try:
        rows = list(read_inputs(cfg, ["folder"], True))
        assert len(rows) == 1 and rows[0][1].error == "unsafe_path"
    finally:
        os.rmdir(link) if os.name == "nt" else link.unlink()


def test_streaming_api_and_dry_batch_rollback(cfg, ledger):
    with TestClient(create_app(cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        assert client.post("/api/file?name=x.png", content=b"x").status_code == 403
        batch = client.post("/api/dry-batch", headers=headers).json()["batch_id"]
        statuses = []
        for value in ("base", "boundary"):
            response = client.post(
                "/api/file",
                params={"name": "folder/a.json", "batch_id": batch},
                content=payload(value),
                headers=headers,
            )
            statuses.append(response.json()["results"][0]["status"])
        assert statuses == ["accepted", "duplicate"]
        client.delete(f"/api/dry-batch/{batch}", headers=headers)
        assert ledger.export()["records"] == []
        response = client.post("/api/file?name=folder/large.png", content=large_png_chunks(), headers=headers)
        assert response.json()["results"][0]["status"] == "accepted"
        assert client.post("/api/file?name=../bad.png", content=b"x", headers=headers).status_code == 400


def test_long_relative_path(cfg):
    parts = ["日本語_long_segment" * 2] * 8
    source = "/".join(parts) + "/sample.json"
    target = cfg.path("input_dir") / source
    try:
        target.parent.mkdir(parents=True)
        target.write_bytes(payload("base"))
    except OSError:
        pytest.skip("OS long-path support unavailable")
    result = list(read_inputs(cfg, [source], False))
    assert extract(source, result[0][1]).positive == "base"


def test_cancelled_cli_transaction_rolls_back(ledger):
    def files():
        yield "a.json", payload("base")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ledger.ingest(files())
    assert ledger.export()["records"] == []
    assert ledger.status()["space"] is None


def test_jpeg_large_tail_does_not_accumulate():
    stream = MetadataStream("large.jpg")
    stream.feed(b"\xff\xd8\xff\xda")
    for _ in range(512):
        stream.feed(b"x" * 65536)
    assert len(stream.output) == 4 and len(stream.part) == 0
    assert stream.finish().error is None


@pytest.mark.parametrize("operation", ["containment", "stat"])
def test_entry_oserror_is_individual(cfg, ledger, operation):
    from prompt_ledger import inputs

    root = cfg.path("input_dir") / "selected"
    root.mkdir(parents=True)
    for name in ("a-good.json", "z-denied.json"):
        (root / name).write_bytes(payload("base"))
    original, original_stat = inputs.contained, Path.is_dir

    def contained(root, name):
        if operation == "containment" and name.endswith("z-denied.json"):
            raise PermissionError("synthetic")
        return original(root, name)

    def is_dir(path):
        if operation == "stat" and path.name == "z-denied.json":
            raise PermissionError("synthetic")
        return original_stat(path)

    with patch.object(inputs, "contained", contained), patch.object(Path, "is_dir", is_dir):
        result = ledger.ingest(read_inputs(cfg, ["selected"], True))
    assert result["processed"] == 2
    assert result["results"][1]["reason"] == "input_io"
    assert len(ledger.export()["records"]) == 1


def test_dry_close_cannot_overtake_enqueue(ledger):
    import threading

    from prompt_ledger.batches import DryBatch

    job = DryBatch(ledger)
    entered, release = threading.Event(), threading.Event()
    original = job.queue.put_nowait
    from prompt_ledger.config import LedgerError as inputs_error

    outcomes = []

    def put(item):
        if item is not None:
            entered.set()
            assert release.wait(2)
        return original(item)

    def submit():
        try:
            outcomes.append(job.submit("a.json", payload("base")))
        except inputs_error as exc:
            outcomes.append(exc)

    with patch.object(job.queue, "put_nowait", put):
        sender = threading.Thread(target=submit)
        sender.start()
        assert entered.wait(2)
        closer = threading.Thread(target=job.close)
        closer.start()
        assert not job.closed.wait(0.05)
        release.set()
        sender.join(2)
        closer.join(2)
    assert not sender.is_alive() and not closer.is_alive()
    assert len(outcomes) == 1 and job.finished.is_set() and job.queue.empty()
    assert ledger.export()["records"] == []
    with pytest.raises(Exception, match="expired"):
        job.submit("b.json", payload("base"))
