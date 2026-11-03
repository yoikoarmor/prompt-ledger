import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from prompt_ledger.config import LedgerError
from prompt_ledger.embedding import QwenEmbedder


@pytest.mark.parametrize(
    "requested_device,available,expected",
    [("cpu", True, "cpu"), ("cuda", True, "cuda"), ("auto", False, "cpu"), ("auto", True, "cuda")],
)
def test_device_selection_no_real_gpu(cfg, monkeypatch, requested_device, available, expected):
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available))
    )
    embedder = QwenEmbedder(replace(cfg, device=requested_device))
    assert embedder.select_device() == expected
    assert embedder.execution()["requested"] == requested_device
    assert embedder.execution()["loaded"] is False
    if requested_device == "auto" and not available:
        assert embedder.execution()["fallback_reason"] == "cuda_unavailable_cpu_fallback"


def test_explicit_cuda_unavailable_and_oom(cfg, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    embedder = QwenEmbedder(replace(cfg, device="cuda"))
    with pytest.raises(LedgerError) as error:
        embedder.select_device()
    assert error.value.code == "cuda_unavailable"
    embedder.device = "cuda"

    def fail(_):
        raise RuntimeError("CUDA out of memory synthetic")

    monkeypatch.setattr(embedder, "_encode", fail)
    with pytest.raises(LedgerError) as error:
        embedder.encode("synthetic")
    assert error.value.code == "cuda_oom" and embedder.device == "cuda"


def test_pair_gpu_batch_budget_and_token_limit_without_gpu(cfg):
    np = pytest.importorskip("numpy")

    class Model:
        def __init__(self):
            self.batches = []

        def tokenizer(self, text, **kwargs):
            return {"input_ids": [0] * len(text)}

        def encode(self, texts, **kwargs):
            self.batches.append(kwargs["batch_size"])
            return np.asarray([[1.0] + [0.0] * 1023 for _ in texts])

    embedder = QwenEmbedder(replace(cfg, device="cuda"))
    embedder.device = "cuda"
    embedder.model = Model()
    assert len(embedder.encode_pair("a", "b")) == 2
    assert embedder.model.batches == [2]
    embedder.encode_pair("a" * 3000, "b")
    assert embedder.model.batches[-1] == 1
    with pytest.raises(LedgerError) as exc:
        embedder.encode_pair("a" * 32769, "b")
    assert exc.value.code == "token_limit"
    assert len(embedder.model.batches) == 2
