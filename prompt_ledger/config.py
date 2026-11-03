import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_METADATA = 1024 * 1024
MAX_TEXT = 128 * 1024
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"


class LedgerError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise LedgerError("unsafe_path", "Use a nonempty forward-slash relative path.")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or any(p in ("..", ".") for p in value.split("/")):
        raise LedgerError("unsafe_path", "Absolute paths and dot segments are not allowed.")
    if any(":" in p for p in path.parts):
        raise LedgerError("unsafe_path", "Alternate data streams are not allowed.")
    return path


def contained(root, value):
    path = relative_path(value)
    root = Path(root).resolve()
    current = root
    for part in path.parts:
        current = current / part
        # Reject symlinks and Windows junctions, including links that remain inside root.
        if current.is_symlink() or (current.exists() and current.resolve() != current):
            raise LedgerError("unsafe_path", "Links and junctions are not allowed.")
    if not current.resolve().is_relative_to(root):
        raise LedgerError("unsafe_path", "Path escapes the selected root.")
    return current


@dataclass(frozen=True)
class Config:
    root: Path
    threshold: float = 0.95
    data_dir: str = "data"
    model_dir: str = "models/qwen3-embedding-0.6b"
    input_dir: str = "input"
    output_dir: str = "outputs"
    device: str = "cpu"

    @classmethod
    def load(cls, root=None):
        # Source checkout is the default; installed applications should use --root or LEDGER_ROOT.
        root = Path(root or os.environ.get("LEDGER_ROOT") or Path(__file__).resolve().parents[1]).resolve()
        cfg = root / "config.json"
        values = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
        if values.pop("schema_version", 1) != 1:
            raise LedgerError("config", "Unsupported configuration schema.")
        if set(values) - {"threshold", "data_dir", "model_dir", "input_dir", "output_dir", "device"}:
            raise LedgerError("config", "Unknown configuration key.")
        result = cls(root, **values)
        if result.device not in ("cpu", "cuda", "auto"):
            raise LedgerError("device", "Device must be cpu, cuda or auto.")
        if not isinstance(result.threshold, (float, int)) or not 0 < result.threshold <= 1:
            raise LedgerError("config", "Threshold must be in (0, 1].")
        for key in ("data_dir", "model_dir", "input_dir", "output_dir"):
            contained(root, getattr(result, key))
        return result

    def path(self, key):
        return contained(self.root, getattr(self, key))
