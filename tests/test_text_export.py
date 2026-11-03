import json
from unittest.mock import patch

from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.text_export import export_text
from prompt_ledger.web import create_app


def test_text_export_no_embeddings_and_exclusion(ledger):
    ledger.set_rules(["blocked"], 0)
    with patch.object(ledger.embedder, "encode", side_effect=AssertionError("must not embed")):
        result = export_text(
            ledger,
            [
                ("a.json", payload("base")),
                ("b.json", payload("base")),
                ("c.json", payload("blocked")),
                ("d.json", b"{}"),
            ],
            "metadata.json",
        )
    data = json.loads((ledger.cfg.root / result["path"]).read_text(encoding="utf-8"))
    assert result["counts"] == {"extracted": 2, "excluded": 1, "review": 1}
    assert data["records"][0]["positive"] == "base"
    assert "positive" not in data["records"][2]
    assert ledger.status()["space"] is None
    assert ledger.records()["records"] == []


def test_explicit_api_extraction_returns_text_without_db_rows(ledger):
    with TestClient(create_app(ledger.cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        assert (
            client.post("/api/file?name=a.json&extract_only=true", content=payload("base")).status_code == 403
        )
        response = client.post(
            "/api/file?name=a.json&extract_only=true", content=payload("base"), headers=headers
        )
        assert response.json()["record"]["positive"] == "base"
        assert ledger.records()["records"] == []


def test_cli_extraction_without_model(cfg):
    from prompt_ledger.cli import execute, parser

    root = cfg.path("input_dir")
    root.mkdir(parents=True)
    (root / "synthetic.json").write_bytes(payload("base"))
    result = execute(parser().parse_args(["--root", str(cfg.root), "extract", "synthetic.json"]))
    assert result["counts"] == {"extracted": 1}


def test_extraction_interruption_preserves_previous(ledger):
    import pytest

    target = ledger.cfg.path("output_dir") / "metadata.json"
    target.parent.mkdir(parents=True)
    target.write_text("previous")

    def files():
        yield "a.json", payload("base")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        export_text(ledger, files(), "metadata.json")
    assert target.read_text() == "previous"
    assert list(target.parent.glob(".extract-*")) == []
