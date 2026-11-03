from dataclasses import replace

import pytest

from prompt_ledger.config import LedgerError
from prompt_ledger.embedding import QwenEmbedder


@pytest.mark.parametrize("device,expected", [("cpu", 1), ("cuda", 8)])
def test_bounded_text_batch_without_model_or_gpu(cfg, device, expected):
    np = pytest.importorskip("numpy")

    class Model:
        def __init__(self):
            self.calls = []

        def tokenizer(self, text, **kwargs):
            assert kwargs["truncation"] is False
            return {"input_ids": [0] * len(text)}

        def encode(self, texts, **kwargs):
            self.calls.append(kwargs)
            return np.asarray([[1.0] + [0.0] * 1023 for _ in texts])

    embedder = QwenEmbedder(replace(cfg, device=device))
    embedder.device = device
    embedder.model = Model()
    assert len(embedder.encode_texts(["a"] * 16)) == 16
    assert embedder.model.calls[-1]["batch_size"] == expected
    embedder.encode_texts(["x" * 1000] * 8)
    assert embedder.model.calls[-1]["batch_size"] == (4 if device == "cuda" else 1)
    count = len(embedder.model.calls)
    with pytest.raises(LedgerError) as error:
        embedder.encode_texts(["x" * 32769])
    assert error.value.code == "token_limit" and len(embedder.model.calls) == count


def test_cuda_text_oom_stops_without_embedding_cpu_fallback(cfg):
    embedder = QwenEmbedder(replace(cfg, device="cuda"))
    embedder.device = "cuda"
    embedder.model = object()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic CUDA out of memory")

    embedder._encode_many = fail
    with pytest.raises(LedgerError) as error:
        embedder.encode_texts(["synthetic"])
    assert error.value.code == "cuda_oom"
    assert embedder.device == "cuda" and embedder.model is None
