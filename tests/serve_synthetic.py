"""Opt-in local UI fixture server. Never reads input images or runs a real model.

Run: python tests/serve_synthetic.py --root .cache/ui-fixture
This is deliberately not exposed as a production CLI backend.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from conftest import FakeEmbedder, payload

from prompt_ledger.config import Config
from prompt_ledger.core import Ledger
from prompt_ledger.web import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    cfg = Config.load(args.root)
    ledger = Ledger(cfg, FakeEmbedder())
    ledger.ingest(
        [
            ("synthetic-first.json", payload("base")),
            ("synthetic-boundary.json", payload("boundary")),
            ("synthetic-review.json", b"{}"),
        ]
    )
    uvicorn.run(create_app(cfg, ledger, port=8767), host="127.0.0.1", port=8767, access_log=False)
