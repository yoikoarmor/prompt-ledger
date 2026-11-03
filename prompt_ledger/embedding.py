import hashlib
import importlib.metadata
import json
import math
import re
import threading

from .config import MODEL_ID, LedgerError, contained

MAX_TOKENS = 32768


def runtime_versions():
    versions = {}
    for name in ("sentence-transformers", "transformers", "torch"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def normalize(vector, dimension):
    if len(vector) != dimension or not all(math.isfinite(x) for x in vector):
        raise LedgerError("embedding", "Embedding has invalid dimension or nonfinite values.")
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise LedgerError("embedding", "Zero or nonfinite embedding is not usable.")
    return [float(x / norm) for x in vector]


def cosine(a, b):
    if len(a) != len(b):
        raise LedgerError("model_mismatch", "Embedding dimensions differ.")
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b))))


class QwenEmbedder:
    """Lazy and offline; CPU default with explicit CUDA selection. Setup is separate."""

    def __init__(self, cfg):
        self.directory = cfg.path("model_dir")
        self.requested_device = cfg.device
        self.device = None
        self.device_reason = None
        self.model = None
        self.loaded_identity = None
        self.lock = threading.Lock()

    def select_device(self):
        if self.device is not None:
            return self.device
        if self.requested_device == "cpu":
            self.device = "cpu"
            return self.device
        try:
            import torch

            available = torch.cuda.is_available()
        except (ImportError, RuntimeError):
            available = False
        if not available and self.requested_device == "cuda":
            raise LedgerError(
                "cuda_unavailable",
                "CUDA is unavailable. Install the CUDA extra and a compatible driver, or select cpu.",
            )
        self.device = "cuda" if available else "cpu"
        if not available:
            self.device_reason = "cuda_unavailable_cpu_fallback"
        return self.device

    def execution(self):
        return {
            "requested": self.requested_device,
            "selected": self.device,
            "loaded": self.model is not None,
            "fallback_reason": self.device_reason,
        }

    @property
    def identity(self):
        if self.loaded_identity is not None:
            return dict(self.loaded_identity)
        manifest = contained(self.directory, "manifest.json")
        if not manifest.exists():
            raise LedgerError("model_missing", "Run setup-model with an immutable revision before importing.")
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if value.get("model_id") != MODEL_ID or not re.fullmatch(r"[0-9a-f]{40}", value.get("revision", "")):
            raise LedgerError("model_manifest", "Invalid model manifest.")
        return {
            "model_id": MODEL_ID,
            "revision": value["revision"],
            "dimension": 1024,
            "normalization": "l2-float64-v1",
            "encoding": "symmetric-no-instruction-v1",
            "max_tokens": MAX_TOKENS,
            "dtype": "float32",
            "device": self.select_device(),
            "runtime_versions": runtime_versions(),
            "artifact_hash": hashlib.sha256(
                json.dumps(value.get("sha256", {}), sort_keys=True).encode()
            ).hexdigest(),
        }

    def encode(self, text):
        try:
            return self._encode(text)
        except RuntimeError as exc:
            if self.device == "cuda":
                self.model = None
                code = "cuda_oom" if "out of memory" in str(exc).lower() else "cuda_runtime"
                raise LedgerError(
                    code,
                    "CUDA inference failed. Free VRAM or retry with an explicit CPU/new ledger root; no silent fallback occurred.",
                ) from None
            raise

    def _encode(self, text):
        with self.lock:
            if self.model is None:
                identity = self.identity
                manifest = json.loads(contained(self.directory, "manifest.json").read_text(encoding="utf-8"))
                files = manifest.get("sha256", {})
                if not files or not any(name.endswith(".safetensors") for name in files):
                    raise LedgerError("model_manifest", "Model files have no integrity manifest.")
                for name, expected in files.items():
                    file = contained(self.directory, name)
                    digest = hashlib.sha256()
                    with file.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != expected:
                        raise LedgerError("model_manifest", "Model integrity check failed; repeat setup.")
                try:
                    import torch
                    from sentence_transformers import SentenceTransformer
                except ImportError:
                    raise LedgerError(
                        "model_dependency", "Install the model extra with uv sync --extra model."
                    ) from None
                torch.set_num_threads(2)
                self.model = SentenceTransformer(
                    str(self.directory),
                    device=self.select_device(),
                    local_files_only=True,
                    trust_remote_code=False,
                    model_kwargs={"torch_dtype": torch.float32},
                    tokenizer_kwargs={"padding_side": "left"},
                )
                self.model.max_seq_length = MAX_TOKENS
                self.model.default_prompt_name = None
                if self.model.get_sentence_embedding_dimension() != identity["dimension"]:
                    self.model = None
                    raise LedgerError("model_mismatch", "Expected a full 1024-dimensional Qwen model.")
                self.loaded_identity = identity
            token_ids = self.model.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
            if len(token_ids) > MAX_TOKENS:
                raise LedgerError(
                    "token_limit", "Prompt exceeds 32768 tokens including special tokens; not truncated."
                )
            vector = self.model.encode(
                [text], prompt="", normalize_embeddings=False, show_progress_bar=False, convert_to_numpy=True
            )[0]
            return normalize(vector.tolist(), 1024)


def setup_model(cfg, revision):
    from .model_setup import acquire

    return acquire(cfg, revision)
