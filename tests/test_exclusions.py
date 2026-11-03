import base64
import json

import pytest
from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.cli import main
from prompt_ledger.config import LedgerError
from prompt_ledger.core import Ledger
from prompt_ledger.web import create_app


def test_model_free_exclusion_persistence_and_reimport(cfg):
    ledger = Ledger(cfg)
    assert ledger.rules()["count"] == 0
    ledger.set_rules(["FLOWER", "blur", ".*"], 0)
    records = ledger.ingest(
        [
            ("a.json", payload("a flower")),
            ("b.json", payload("base", "BLUR")),
            ("c.json", payload("a literal .* token")),
        ]
    )["results"]
    assert all(row["status"] == "excluded" for row in records)
    assert ledger.status()["space"] is None
    assert ledger.export()["records"] == []
    fresh = Ledger(cfg)
    assert fresh.rules(False)["terms"] == ["FLOWER", "blur", ".*"]
    assert fresh.ingest([("a.json", payload("a flower"))])["results"][0]["reused"]
    assert "FLOWER" not in json.dumps(fresh.records())
    assert fresh.records(False)["records"][-1]["original_matched_terms"] == ["FLOWER"]


def test_literal_casefold_and_no_regex(ledger, fake):
    ledger.set_rules(["STRASSE", ".*"], 0)
    result = ledger.ingest([("a.json", payload("Straße")), ("b.json", payload("base"))])["results"]
    assert [r["status"] for r in result] == ["excluded", "accepted"]
    assert fake.calls == 1


def test_edit_rules_filters_existing_export_without_deleting(ledger, fake):
    ledger.ingest([("a.json", payload("base"))])
    calls = fake.calls
    ledger.set_rules(["BASE"], 0)
    assert ledger.export()["records"] == []
    assert ledger.records()["records"][0]["status"] == "excluded"
    assert ledger.records()["records"][0]["stored_status"] == "accepted"
    assert ledger.ingest([("a.json", payload("base"))])["results"][0]["status"] == "excluded"
    assert fake.calls == calls
    result = ledger.export_file()
    assert result["count"] == 0
    ledger.set_rules([], 1)
    assert len(ledger.export()["records"]) == 1


def test_removed_rule_requires_reimport_for_original_exclusion(ledger):
    ledger.set_rules(["base"], 0)
    ledger.ingest([("a.json", payload("base"))])
    ledger.set_rules([], 1)
    assert ledger.export()["records"] == []
    assert ledger.ingest([("a.json", payload("base"))])["results"][0]["status"] == "accepted"
    assert len(ledger.records()["records"]) == 2
    assert len(ledger.export()["records"]) == 1


def test_review_correction_and_dry_run_cannot_bypass(ledger, fake):
    record_id = ledger.ingest([("bad.json", b"{}")])["results"][0]["id"]
    ledger.set_rules(["base"], 0)
    result = ledger.resolve(record_id, "base", "")["result"]
    assert result["status"] == "excluded" and fake.calls == 0
    assert ledger.export()["records"] == []
    before = ledger.status()["counts"]
    ledger.ingest([("other.json", payload("base again"))], dry_run=True)
    assert ledger.status()["counts"] == before


def test_excluded_token_review_cannot_resolve_self_or_other(ledger, fake):
    original_encode = fake.encode

    def encode(text):
        if text == "synthetic too long":
            raise LedgerError("token_limit", "Synthetic limit")
        return original_encode(text)

    fake.encode = encode
    token_id = ledger.ingest([("long.json", payload("synthetic too long"))])["results"][0]["id"]
    other_id = ledger.ingest([("bad.json", b"{}")])["results"][0]["id"]
    ledger.set_rules(["too long"], 0)
    for record_id in (token_id, other_id):
        result = ledger.resolve(record_id, "synthetic too long", "")["result"]
        assert result["status"] == "excluded"
        assert result["stored_status"] == "review"
        assert result["resolved_by"] is None
    assert all(r["resolved_by"] is None for r in ledger.records()["records"])
    assert ledger.status()["pending_review"] == 2
    ledger.set_rules([], 1)
    for record_id in (token_id, other_id):
        assert ledger.resolve(record_id, "base", "")["result"]["status"] == "accepted"
    assert ledger.status()["pending_review"] == 0


def test_unparseable_review_can_retry_after_excluded_correction(cfg, ledger):
    with TestClient(create_app(cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        review_id = ledger.ingest([("bad.json", b"{}")])["results"][0]["id"]
        ledger.set_rules(["base"], 0)
        body = {"positive": "base", "negative": ""}
        response = client.post(f"/api/review/{review_id}", json=body, headers=headers)
        excluded_id = response.json()["result"]["id"]
        assert response.json()["result"]["status"] == "excluded"
        assert ledger.export()["records"] == []
        assert ledger.status()["pending_review"] == 1
        ledger.set_rules([], 1)
        assert ledger.ingest([("bad.json", b"{}")])["results"][0]["resolved_by"] is None
        response = client.post(f"/api/review/{review_id}", json=body, headers=headers)
        assert response.json()["result"]["status"] == "accepted"
        rows = {r["id"]: r for r in ledger.records()["records"]}
        assert rows[excluded_id]["status"] == "excluded"
        assert rows[review_id]["resolved_by"] == response.json()["result"]["id"]
        assert len(ledger.export()["records"]) == 1
        assert client.post(f"/api/review/{review_id}", json=body, headers=headers).status_code == 409


@pytest.mark.parametrize("terms", [[""], [" "], ["a\nb"], ["x" * 257], ["a"] * 101, "a", [None]])
def test_invalid_rules(ledger, terms):
    with pytest.raises(LedgerError):
        ledger.set_rules(terms, 0)
    assert ledger.rules()["version"] == 0


def test_rules_conflict_and_no_plaintext_response(ledger):
    result = ledger.set_rules(["private synthetic rule"], 0)
    assert "private" not in json.dumps(result)
    assert "private" not in json.dumps(ledger.rules())
    with pytest.raises(LedgerError) as error:
        ledger.set_rules([], 0)
    assert error.value.code == "rules_conflict"


def test_rule_matching_does_not_join_positive_and_negative(ledger):
    ledger.set_rules(["baseblur"], 0)
    assert ledger.ingest([("a.json", payload("base", "blur"))])["results"][0]["status"] == "accepted"


def test_concurrent_rule_edits_preserve_first_version(cfg):
    from concurrent.futures import ThreadPoolExecutor

    ledgers = [Ledger(cfg), Ledger(cfg)]

    def edit(item):
        try:
            return item.set_rules(["synthetic"], 0)["version"]
        except LedgerError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, ledgers))
    assert sorted(outcomes, key=str) == [1, "rules_conflict"]


def test_api_and_cli_share_rules_and_export(cfg, ledger, capsys):
    with TestClient(create_app(cfg, ledger), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        assert client.put("/api/rules", json={"terms": ["base"], "expected_version": 0}).status_code == 403
        assert (
            client.put(
                "/api/rules", json={"terms": ["base"], "expected_version": 0}, headers=headers
            ).status_code
            == 200
        )
        assert "base" not in client.get("/api/rules", headers=headers).text
        assert client.get("/api/rules?privacy=false", headers=headers).json()["terms"] == ["base"]
        body = {"files": [{"name": "x.json", "content_base64": base64.b64encode(payload("base")).decode()}]}
        assert (
            client.post("/api/import", json=body, headers=headers).json()["results"][0]["status"]
            == "excluded"
        )
        assert client.get("/api/export", headers=headers).json()["records"] == []
        cfg.path("input_dir").mkdir()
        (cfg.path("input_dir") / "x.json").write_bytes(payload("base"))
        assert main(["--root", str(cfg.root), "import", "x.json"]) == 0
        assert json.loads(capsys.readouterr().out)["results"][0]["status"] == "excluded"
        assert main(["--root", str(cfg.root), "export"]) == 0
        assert json.loads(capsys.readouterr().out)["count"] == 0
        assert (
            main(["--root", str(cfg.root), "rules", "--set-json", '["new"]', "--expected-version", "1"]) == 0
        )
        capsys.readouterr()
        assert client.get("/api/rules?privacy=false", headers=headers).json()["terms"] == ["new"]
