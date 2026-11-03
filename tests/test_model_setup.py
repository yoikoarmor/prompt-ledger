import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from prompt_ledger.config import MODEL_ID, LedgerError
from prompt_ledger.model_setup import DEFAULT_REVISION, acquire


def fake_hub(monkeypatch, fail=False):
    content = {"config.json": b"{}", "model.safetensors": b"synthetic weights only"}
    calls = []
    siblings = [
        SimpleNamespace(rfilename=n, size=len(b), lfs=SimpleNamespace(sha256=hashlib.sha256(b).hexdigest()))
        for n, b in content.items()
    ]

    class API:
        def __init__(self, **kwargs):
            assert kwargs == {"endpoint": "https://huggingface.co", "token": False}

        def model_info(self, *args, **kwargs):
            return SimpleNamespace(sha=DEFAULT_REVISION, siblings=siblings)

    def download(repo, name, **kwargs):
        calls.append((name, kwargs["force_download"]))
        if fail and name.endswith(".safetensors"):
            raise OSError("synthetic interruption")
        (kwargs["local_dir"] / name).write_bytes(content[name])

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=API, hf_hub_download=download))
    return calls


def test_resume_verified_skip_and_repair(cfg, monkeypatch):
    calls = fake_hub(monkeypatch, fail=True)
    with pytest.raises(LedgerError, match="OSError"):
        acquire(cfg, DEFAULT_REVISION)
    directory = cfg.path("model_dir")
    assert not (directory / "manifest.json").exists()
    assert not (directory / ".setup.lock").exists()
    calls = fake_hub(monkeypatch)
    result = acquire(cfg, DEFAULT_REVISION)
    assert result["files"] == 2 and calls == [("model.safetensors", False)]
    calls.clear()
    assert acquire(cfg, DEFAULT_REVISION)["reused"] and not calls
    (directory / "model.safetensors").write_bytes(b"corrupted synthetic")
    acquire(cfg, DEFAULT_REVISION)
    assert calls == [("model.safetensors", True)]
    assert len(json.loads((directory / "manifest.json").read_text())["sha256"]) == 2


def test_preserve_unknown_files_and_different_revision(cfg, monkeypatch):
    directory = cfg.path("model_dir")
    directory.mkdir(parents=True)
    keep = directory / "unknown.txt"
    keep.write_text("synthetic preserve")
    with pytest.raises(LedgerError, match="Unrecognized"):
        acquire(cfg, DEFAULT_REVISION)
    assert keep.read_text() == "synthetic preserve"


def test_tls_error_is_explicit_and_no_manifest(cfg, monkeypatch):
    class API:
        def __init__(self, **kwargs):
            pass

        def model_info(self, *args, **kwargs):
            raise OSError("CERTIFICATE_VERIFY_FAILED synthetic")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=API))
    with pytest.raises(LedgerError, match="TLS certificate verification failed"):
        acquire(cfg, DEFAULT_REVISION)
    assert not (cfg.path("model_dir") / "manifest.json").exists()


def test_setup_lock_and_disk_space_fail_closed(cfg, monkeypatch):
    from prompt_ledger import model_setup

    directory = cfg.path("model_dir")
    directory.mkdir(parents=True)
    lock = directory / ".setup.lock"
    lock.touch()
    with pytest.raises(LedgerError, match="Setup lock exists"):
        acquire(cfg, DEFAULT_REVISION)
    lock.unlink()
    calls = fake_hub(monkeypatch)
    monkeypatch.setattr(model_setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(LedgerError, match="Insufficient"):
        acquire(cfg, DEFAULT_REVISION)
    assert not calls and not (directory / "manifest.json").exists()


def test_local_manifest_cannot_certify_modified_weights(cfg, monkeypatch):
    calls = fake_hub(monkeypatch)
    acquire(cfg, DEFAULT_REVISION)
    directory = cfg.path("model_dir")
    path = directory / "model.safetensors"
    path.write_bytes(b"locally substituted synthetic weights")
    manifest = directory / "manifest.json"
    saved = json.loads(manifest.read_text())
    saved["sha256"]["model.safetensors"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(saved))
    (directory / ".setup.json").unlink()
    calls.clear()
    assert not acquire(cfg, DEFAULT_REVISION)["reused"]
    assert calls == [("model.safetensors", True)]
    assert path.read_bytes() == b"synthetic weights only"


@pytest.mark.parametrize(
    "files",
    [
        {},
        [],
        None,
        {"model.safetensors": {}},
        {"model.safetensors": {"algorithm": "sha256", "size": -1, "hash": "a" * 64}},
    ],
)
def test_invalid_resume_inventory_never_succeeds(cfg, monkeypatch, files):
    calls = fake_hub(monkeypatch)
    directory = cfg.path("model_dir")
    directory.mkdir(parents=True)
    state = directory / ".setup.json"
    raw = json.dumps({"model_id": MODEL_ID, "revision": DEFAULT_REVISION, "files": files})
    state.write_text(raw)
    with pytest.raises(LedgerError, match="inventory|metadata|size"):
        acquire(cfg, DEFAULT_REVISION)
    assert state.read_text() == raw and not calls
    assert not (directory / "manifest.json").exists()


def test_resume_hashes_are_not_authenticity_authority(cfg, monkeypatch):
    calls = fake_hub(monkeypatch)
    acquire(cfg, DEFAULT_REVISION)
    directory = cfg.path("model_dir")
    path = directory / "model.safetensors"
    path.write_bytes(b"forged synthetic")
    state = directory / ".setup.json"
    plan = json.loads(state.read_text())
    plan["files"]["model.safetensors"].update(
        size=path.stat().st_size, hash=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    state.write_text(json.dumps(plan))
    calls.clear()
    acquire(cfg, DEFAULT_REVISION)
    assert calls == [("model.safetensors", True)]


def test_verified_local_copy_still_requires_official_metadata(cfg, monkeypatch):
    fake_hub(monkeypatch)
    acquire(cfg, DEFAULT_REVISION)
    manifest = cfg.path("model_dir") / "manifest.json"
    before = manifest.read_bytes()

    class API:
        def __init__(self, **kwargs):
            pass

        def model_info(self, *args, **kwargs):
            raise OSError("synthetic offline")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=API))
    with pytest.raises(LedgerError, match="OSError"):
        acquire(cfg, DEFAULT_REVISION)
    assert manifest.read_bytes() == before


def test_empty_official_inventory_fails(cfg, monkeypatch):
    class API:
        def __init__(self, **kwargs):
            pass

        def model_info(self, *args, **kwargs):
            return SimpleNamespace(sha=DEFAULT_REVISION, siblings=[])

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=API))
    with pytest.raises(LedgerError, match="no weights"):
        acquire(cfg, DEFAULT_REVISION)
    assert not (cfg.path("model_dir") / "manifest.json").exists()


@pytest.mark.parametrize(
    "relative",
    [
        ".cache",
        ".cache/huggingface",
        ".cache/huggingface/download/nested",
        ".cache/huggingface/download/model.safetensors.metadata",
        ".cache/huggingface/download/model.safetensors.lock",
        ".cache/huggingface/download/partial.incomplete",
    ],
)
def test_cache_junction_rejected_without_following(cfg, monkeypatch, tmp_path, relative):
    directory = cfg.path("model_dir")
    directory.mkdir(parents=True)
    outside = tmp_path / "synthetic-outside-model"
    outside.mkdir()
    marker = outside / "owner-marker.txt"
    marker.write_text("preserve")
    link = directory / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if result.returncode:
            pytest.skip("Windows junction creation unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    calls = fake_hub(monkeypatch)
    try:
        assert link.resolve() == outside.resolve()
        with pytest.raises(LedgerError, match="Links/reparse"):
            acquire(cfg, DEFAULT_REVISION)
        assert not calls
        assert link.resolve() == outside.resolve()
        assert marker.read_text() == "preserve"
        assert sorted(p.name for p in outside.iterdir()) == ["owner-marker.txt"]
        assert not (directory / ".setup.lock").exists()
    finally:
        # Remove only this synthetic link, never recurse into its target.
        assert link.parent.resolve().is_relative_to(directory.resolve())
        assert link.resolve() == outside.resolve()
        os.rmdir(link) if os.name == "nt" else link.unlink()


def test_nested_cache_file_symlink_rejected(tmp_path):
    from prompt_ledger.model_setup import validate_hub_cache

    root = tmp_path / "model"
    cache = root / ".cache/huggingface/download"
    cache.mkdir(parents=True)
    link = cache / "partial.incomplete"
    try:
        link.symlink_to(tmp_path / "missing-target")
    except OSError:
        pytest.skip("File symlink creation unavailable")
    with pytest.raises(LedgerError, match="Links/reparse"):
        validate_hub_cache(root)
