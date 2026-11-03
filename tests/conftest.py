import hashlib
import json
import math
from typing import ClassVar

import pytest

from prompt_ledger.config import Config
from prompt_ledger.core import Ledger


class FakeEmbedder:
    """Synthetic mathematical vectors, never a substitute for semantic Qwen validation."""

    identity: ClassVar[dict] = {
        "model_id": "synthetic-test-only",
        "revision": "v1",
        "dimension": 3,
        "normalization": "l2-float64-v1",
        "encoding": "test-v1",
    }

    def __init__(self):
        self.calls = 0

    def encode(self, text):
        self.calls += 1
        known = {
            "base": [1, 0, 0],
            "boundary": [0.95, math.sqrt(1 - 0.95**2), 0],
            "below": [0.9499, math.sqrt(1 - 0.9499**2), 0],
            "opposite": [-1, 0, 0],
            "negative-a": [0, 1, 0],
            "negative-b": [0, -1, 0],
        }
        return known.get(text, [x / 255 for x in hashlib.sha256(text.encode()).digest()[:3]])


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / "日本語 spaced root"
    root.mkdir()
    return Config.load(root)


@pytest.fixture
def fake():
    return FakeEmbedder()


@pytest.fixture
def ledger(cfg, fake):
    return Ledger(cfg, fake)


def payload(positive, negative=""):
    return json.dumps({"positive": positive, "negative": negative}).encode()
