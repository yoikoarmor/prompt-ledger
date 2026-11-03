"""Official pinned model download with verified, resumable files; never inference."""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile

from .config import MODEL_ID, LedgerError, contained

DEFAULT_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
PATTERNS = ("*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "LICENSE*", "NOTICE*")


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    if algorithm == "sha1":
        h.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".setup-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def network_error(exc):
    certificate = "CERTIFICATE_VERIFY_FAILED" in str(exc) or "certificate verify failed" in str(exc)
    reason = "TLS certificate verification failed" if certificate else type(exc).__name__
    return LedgerError(
        "model_download", f"huggingface.co: {reason}. TLS remains enabled; resolve the cause and rerun."
    )


def validate_hub_cache(directory):
    """Inspect existing cache entries with lstat; never descend through a link."""
    pending = [directory / ".cache"]
    while pending:
        path = pending.pop()
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise LedgerError("unsafe_path", "Links/reparse points in the model cache are not allowed.")
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(path / entry.name for entry in entries)
        elif not stat.S_ISREG(info.st_mode):
            raise LedgerError("unsafe_path", "Only regular files/directories are allowed in the model cache.")


def acquire(cfg, revision):
    if not re.fullmatch("[0-9a-f]{40}", revision or ""):
        raise LedgerError("revision", "Supply an immutable 40-character official commit SHA.")
    directory = cfg.path("model_dir")
    validate_hub_cache(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = contained(directory, ".setup.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise LedgerError(
            "setup_busy", "Setup lock exists; check other setup processes. See README."
        ) from None
    os.close(fd)
    try:
        return acquire_locked(directory, revision)
    finally:
        lock.unlink()


def validate_files(directory, files):
    if (
        not isinstance(files, dict)
        or not files
        or not any(isinstance(name, str) and name.endswith(".safetensors") for name in files)
    ):
        raise LedgerError("model_metadata", "Model file inventory is empty or has no weights.")
    for name, item in files.items():
        contained(directory, name)
        if not isinstance(item, dict):
            raise LedgerError("model_metadata", "Invalid file metadata.")
        algorithm = item.get("algorithm")
        size = item.get("size")
        expected = item.get("hash")
        if (
            algorithm not in ("sha256", "sha1")
            or type(size) is not int
            or size < 0
            or not isinstance(expected, str)
            or not re.fullmatch(rf"[0-9a-f]{{{64 if algorithm == 'sha256' else 40}}}", expected)
        ):
            raise LedgerError("model_metadata", "Invalid file size, algorithm or hash.")


def official_plan(directory, revision):
    try:
        from huggingface_hub import HfApi

        info = HfApi(endpoint="https://huggingface.co", token=False).model_info(
            MODEL_ID, revision=revision, files_metadata=True
        )
    except Exception as exc:  # noqa: BLE001 - sanitize provider exceptions
        raise network_error(exc) from None
    if info.sha != revision:
        raise LedgerError("model_metadata", "Official revision does not match requested commit.")
    files = {}
    for item in info.siblings:
        if not any(fnmatch.fnmatch(item.rfilename, pattern) for pattern in PATTERNS):
            continue
        contained(directory, item.rfilename)
        expected = item.lfs.sha256 if item.lfs else item.blob_id
        if item.size is None or not re.fullmatch(rf"[0-9a-f]{{{64 if item.lfs else 40}}}", expected or ""):
            raise LedgerError("model_metadata", "Official file size/hash metadata is incomplete.")
        files[item.rfilename] = {
            "size": item.size,
            "hash": expected,
            "algorithm": "sha256" if item.lfs else "sha1",
        }
    if not any(n.endswith(".safetensors") for n in files):
        raise LedgerError("model_metadata", "Official snapshot has no weights.")
    plan = {"model_id": MODEL_ID, "revision": revision, "files": files}
    validate_files(directory, files)
    return plan


def acquire_locked(directory, revision):
    manifest = contained(directory, "manifest.json")
    state = contained(directory, ".setup.json")
    if manifest.exists():
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if saved.get("model_id") != MODEL_ID or saved.get("revision") != revision:
            raise LedgerError("model_exists", "Different existing model revision; use another root.")
    if state.exists():
        plan = json.loads(state.read_text(encoding="utf-8"))
        if plan.get("model_id") != MODEL_ID or plan.get("revision") != revision:
            raise LedgerError("model_exists", "Partial setup belongs to another revision; use another root.")
        validate_files(directory, plan.get("files"))
    elif not manifest.exists() and any(p.name != ".setup.lock" for p in directory.iterdir()):
        raise LedgerError(
            "model_exists", "Unrecognized existing model files; preserved without modification."
        )
    # Local state is only a resume hint, never an authenticity authority.
    # Every invocation obtains fresh metadata for the immutable official revision.
    plan = official_plan(directory, revision)
    atomic_json(state, plan)
    remaining, hashes = [], {}
    for name, item in plan["files"].items():
        path = contained(directory, name)
        if (
            path.is_file()
            and path.stat().st_size == item["size"]
            and digest(path, item["algorithm"]) == item["hash"]
        ):
            hashes[name] = digest(path)
        else:
            remaining.append(name)
    total = sum(item["size"] for item in plan["files"].values())
    download = sum(plan["files"][name]["size"] for name in remaining)
    required = download * 2 + 256 * 1024 * 1024
    free = shutil.disk_usage(directory).free
    print(f"公式メタデータ: 合計 {total:,} bytes / 残り取得最大目安 {download:,} bytes", file=sys.stderr)
    print(f"モデル作業用空き容量目安 {required:,} bytes / 現在 {free:,} bytes（依存分は別）", file=sys.stderr)
    if free < required:
        raise LedgerError("disk_space", "Insufficient free disk space for model setup.")
    for name in remaining:
        path = contained(directory, name)
        validate_hub_cache(directory)
        try:
            from huggingface_hub import hf_hub_download

            hf_hub_download(
                MODEL_ID,
                name,
                revision=revision,
                local_dir=directory,
                endpoint="https://huggingface.co",
                token=False,
                force_download=path.exists(),
            )
        except Exception as exc:  # noqa: BLE001 - sanitize provider exceptions
            raise network_error(exc) from None
        item = plan["files"][name]
        if path.stat().st_size != item["size"] or digest(path, item["algorithm"]) != item["hash"]:
            raise LedgerError("model_integrity", "Official hash mismatch. Rerun setup to repair the file.")
        hashes[name] = digest(path)
    atomic_json(manifest, {"model_id": MODEL_ID, "revision": revision, "sha256": hashes})
    return {
        "model_id": MODEL_ID,
        "revision": revision,
        "files": len(hashes),
        "bytes": total,
        "reused": not remaining,
    }
