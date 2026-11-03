import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from prompt_ledger.config import MODEL_ID, LedgerError
from prompt_ledger.embedding import MAX_TOKENS, QwenEmbedder


def test_qwen_cpu_offline_and_no_truncation(cfg, monkeypatch):
    directory = cfg.path("model_dir")
    directory.mkdir(parents=True)
    (directory / "model.safetensors").write_bytes(b"synthetic stub, not real weights")
    digest = hashlib.sha256(b"synthetic stub, not real weights").hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps({"model_id": MODEL_ID, "revision": "a" * 40, "sha256": {"model.safetensors": digest}})
    )
    calls = []

    class StubModel:
        def __init__(self, path, **kwargs):
            calls.append(kwargs)

        def get_sentence_embedding_dimension(self):
            return 1024

        def tokenizer(self, text, **kwargs):
            assert kwargs == {"add_special_tokens": True, "truncation": False}
            return {"input_ids": [1] * (MAX_TOKENS + 1 if text == "too long" else 3000)}

        def encode(self, texts, **kwargs):
            assert self.max_seq_length == MAX_TOKENS
            assert kwargs["prompt"] == "" and kwargs["show_progress_bar"] is False
            return [SimpleNamespace(tolist=lambda: [1.0] + [0.0] * 1023)]

    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(float32="float32", set_num_threads=lambda n: None)
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=StubModel))
    embedder = QwenEmbedder(cfg)
    assert len(embedder.encode("synthetic 3000-token input")) == 1024
    assert calls[0]["device"] == "cpu"
    assert calls[0]["local_files_only"] is True
    assert calls[0]["trust_remote_code"] is False
    assert calls[0]["model_kwargs"] == {"torch_dtype": "float32"}
    with pytest.raises(LedgerError, match="not truncated"):
        embedder.encode("too long")
