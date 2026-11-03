import json
import struct
import zlib
from unittest.mock import patch

from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.cli import execute, parser
from prompt_ledger.web import create_app


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"tEXt", b"parameters\0boundary\nNegative prompt: boundary\nSteps: 20, Seed: 1")
        + chunk(b"IEND", b"")
    )


def inputs():
    return [
        ("boundary.png", png()),
        ("negative-different.json", payload("boundary", "below")),
        ("blocked.json", payload("blocked")),
        ("missing.json", b"{}"),
        ("repeat.json", payload("boundary", "below")),
    ]


def prepare(ledger):
    ledger.ingest([("existing.json", payload("base", "base"))])
    ledger.set_rules(["blocked"], 0)


def assert_export(data):
    assert data["schema_version"] == 1
    assert [(r["positive"], r["negative"]) for r in data["records"]] == [
        ("base", "base"),
        ("boundary", "below"),
    ]


def test_image_json_import_compare_export_cli(cfg, ledger):
    prepare(ledger)
    root = cfg.path("input_dir") / "selected"
    root.mkdir(parents=True)
    for name, raw in inputs():
        (root / name).write_bytes(raw)
    with patch("prompt_ledger.cli.Ledger", return_value=ledger):
        result = execute(
            parser().parse_args(
                ["--root", str(cfg.root), "import", *["selected/" + name for name, _ in inputs()]]
            )
        )
        assert [r["status"] for r in result["results"]] == [
            "duplicate",
            "accepted",
            "excluded",
            "review",
            "accepted",
        ]
        assert result["results"][0]["positive_score"] == 0.95
        assert result["results"][0]["negative_score"] == 0.95
        out = execute(parser().parse_args(["--root", str(cfg.root), "export"]))
    assert out["count"] == 2
    assert_export(json.loads((cfg.root / out["path"]).read_text(encoding="utf-8")))


def test_image_json_import_compare_export_api(cfg, ledger):
    prepare(ledger)
    with TestClient(create_app(cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        statuses = []
        for name, raw in inputs():
            statuses.append(
                client.post("/api/file", params={"name": name}, content=raw, headers=headers).json()[
                    "results"
                ][0]["status"]
            )
        assert statuses == ["duplicate", "accepted", "excluded", "review", "accepted"]
        assert_export(client.get("/api/export", headers=headers).json())
        ledger.set_rules(["blocked", "boundary"], 1)
        assert len(client.get("/api/export", headers=headers).json()["records"]) == 1
