import io
import json
import threading
import time
from unittest.mock import patch

import pytest
from conftest import payload
from fastapi.testclient import TestClient

from prompt_ledger.config import LedgerError
from prompt_ledger.json_records import records
from prompt_ledger.pipeline import compare_json
from prompt_ledger.text_export import export_text
from prompt_ledger.web import create_app


def document(items):
    return json.dumps(
        {"schema_version": 1, "kind": "metadata-extraction", "rule_version": 0, "records": items}
    )


def record(p="base", n="", source="synthetic.json"):
    return {"source": source, "status": "extracted", "positive": p, "negative": n}


def source(ledger, text):
    path = ledger.cfg.path("output_dir") / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_extract_then_staged_comparison_preserves_source_and_same_batch(ledger):
    ledger.set_rules(["blocked"], 0)
    result = export_text(
        ledger,
        [
            ("one.json", payload("base")),
            ("two.json", payload("boundary")),
            ("three.json", payload("below")),
            ("four.json", payload("blocked")),
        ],
        "metadata.json",
    )
    src = ledger.cfg.root / result["path"]
    original = src.read_bytes()
    result = compare_json(ledger, src, "compared.json", batch_size=2)
    assert result["counts"] == {"accepted": 2, "duplicate": 1, "excluded": 1}
    out = json.loads((ledger.cfg.root / result["path"]).read_text(encoding="utf-8"))
    assert [x["positive"] for x in out["records"]] == ["base", "below"]
    assert src.read_bytes() == original
    assert out["comparison"]["input_sha256"] == result["input_sha256"]
    duplicate = next(item for item in out["decisions"] if item["status"] == "duplicate")
    assert duplicate["positive_score"] == 0.95 and duplicate["negative_score"] == 1.0
    assert all("positive" not in item and "source" not in item for item in out["decisions"])
    calls = ledger.embedder.calls
    again = compare_json(ledger, src, "again.json", batch_size=2)
    assert ledger.embedder.calls == calls and again["reused"] == 3
    assert again["comparison"]["backend"] == "not_run"
    assert len(ledger.export()["records"]) == 2


def test_bounded_batch_encoding_keeps_and_boundary(ledger):
    batches = []

    def encode(texts):
        batches.append(texts)
        return [ledger.embedder.encode(text) for text in texts]

    ledger.embedder.encode_texts = encode
    src = source(
        ledger,
        document(
            [
                record("base", "base"),
                record("boundary", "boundary"),
                record("boundary", "below"),
                record("blocked"),
                record("base", "base"),
            ]
        ),
    )
    ledger.set_rules(["blocked"], 0)
    result = compare_json(ledger, src, batch_size=2)
    assert result["counts"] == {"accepted": 3, "duplicate": 1, "excluded": 1}
    assert max(map(len, batches)) <= 4
    saved = ledger.records(privacy=False)["records"]
    boundary = next(r for r in saved if r["status"] == "duplicate")
    assert boundary["positive_score"] == boundary["negative_score"] == 0.95


def test_cancellation_retains_committed_prefix_and_replay_reuses_it(ledger):
    src = source(ledger, document([record("base"), record("opposite"), record("below")]))
    target = src.parent / "compared.json"
    target.write_text("previous")
    cancel = threading.Event()

    def progress(value):
        if value["processed"] == 1:
            cancel.set()

    with pytest.raises(LedgerError, match="Cancelled"):
        compare_json(ledger, src, cancel=cancel, progress=progress, batch_size=1)
    assert len(ledger.records()["records"]) == 1
    assert target.read_text() == "previous"
    result = compare_json(ledger, src, batch_size=1)
    assert result["processed"] == 3 and result["reused"] == 1


def test_cancel_current_batch_rolls_back_and_output_old_survives(ledger):
    src = source(ledger, document([record("base"), record("opposite")]))
    cancel = threading.Event()
    original = ledger._add

    def add(*args, **kwargs):
        value = original(*args, **kwargs)
        cancel.set()
        return value

    with patch.object(ledger, "_add", side_effect=add), pytest.raises(LedgerError):
        compare_json(ledger, src, cancel=cancel)
    assert ledger.records()["records"] == []
    assert not list(src.parent.glob(".compare-output-*"))
    assert not list(ledger.cfg.path("data_dir").glob(".compare-source-*"))


def test_rules_change_stops_next_batch_and_replay_applies_current(ledger):
    src = source(ledger, document([record("base"), record("opposite")]))

    def progress(value):
        if value["processed"] == 1:
            ledger.set_rules(["opposite"], 0)

    with pytest.raises(LedgerError) as exc:
        compare_json(ledger, src, batch_size=1, progress=progress)
    assert exc.value.code == "rules_conflict"
    assert compare_json(ledger, src)["counts"] == {"accepted": 1, "excluded": 1}


def test_old_space_rejected_before_batch_inference(ledger):
    ledger.ingest([("old.json", payload("base"))])
    ledger.embedder.encode_texts = lambda _: (_ for _ in ()).throw(AssertionError("no inference"))
    ledger.embedder.identity = {**ledger.embedder.identity, "batching": "new"}
    with pytest.raises(LedgerError) as exc:
        compare_json(ledger, source(ledger, document([record("opposite")])))
    assert exc.value.code == "model_mismatch"


@pytest.mark.parametrize("tail", [",", " {}", "trailing"])
def test_malformed_suffix_never_replaces_output(ledger, tail):
    src = source(ledger, document([record()]) + tail)
    out = src.parent / "compared.json"
    out.write_text("previous")
    with pytest.raises(LedgerError):
        compare_json(ledger, src, batch_size=1)
    assert out.read_text() == "previous"


@pytest.mark.parametrize("invalid", ["../outside.json", "/absolute.json", "C:/private.json"])
def test_unsafe_sources_rejected(ledger, invalid):
    with pytest.raises(LedgerError):
        compare_json(ledger, source(ledger, document([record(source=invalid)])))


def test_source_cannot_be_output(ledger):
    src = source(ledger, document([record()]))
    before = src.read_bytes()
    with pytest.raises(LedgerError):
        compare_json(ledger, src, "metadata.json")
    assert src.read_bytes() == before


def test_reader_split_utf8_escape_and_duplicate_key():
    class Short(io.StringIO):
        def read(self, _=0):
            return super().read(3)

    value = record('日本語" \\ \n')
    assert list(records(Short(document([value])))) == [value]
    bad = '{"schema_version":1,"kind":"metadata-extraction","records":[{"status":"review","status":"extracted"}]}'
    with pytest.raises(LedgerError):
        list(records(io.StringIO(bad)))


def test_token_limit_batch_falls_back_to_individual_review(ledger):
    ledger.embedder.encode_texts = lambda _: (_ for _ in ()).throw(LedgerError("token_limit", "synthetic"))
    encode = ledger.embedder.encode
    ledger.embedder.encode = lambda text: (
        (_ for _ in ()).throw(LedgerError("token_limit", "synthetic")) if text == "long" else encode(text)
    )
    result = compare_json(ledger, source(ledger, document([record("base"), record("long")])))
    assert result["counts"] == {"accepted": 1, "review": 1}


def test_api_json_upload_progress_private_and_result(ledger):
    app = create_app(ledger.cfg, ledger)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        identifier = "a" * 32
        url = f"/api/compare-json?job_id={identifier}&device=cpu"
        assert client.post(url, content=document([record()])).status_code == 403
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        assert (
            client.post(url, content=document([record(), record("boundary")]), headers=headers).status_code
            == 200
        )
        for _ in range(200):
            job = client.get(f"/api/compare-json/{identifier}", headers=headers).json()
            if job["state"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.005)
        assert job["state"] == "done"
        assert "synthetic.json" not in json.dumps(job)
        data = client.get(f"/api/compare-json/{identifier}/result", headers=headers).json()
        assert len(data["records"]) == 1
        assert client.get(f"/api/compare-json/{identifier}/result").status_code == 403
        retained = ledger.cfg.path("output_dir") / f"staged/{identifier}/source.json"
        assert retained.exists()
        assert client.post(url, content=document([]), headers=headers).status_code == 400


def test_cancel_during_export_keeps_old_file_and_source(ledger):
    src = source(ledger, document([record()]))
    target = src.parent / "compared.json"
    target.write_text("previous")
    event = threading.Event()

    def progress(value):
        if value["phase"] == "exporting":
            event.set()

    with pytest.raises(LedgerError):
        compare_json(ledger, src, cancel=event, progress=progress)
    assert target.read_text() == "previous" and src.exists()
    assert not list(src.parent.glob(".compare-output-*"))
    assert len(ledger.export()["records"]) == 1


def test_json_reader_memory_independent_of_document_size(tmp_path):
    import tracemalloc

    path = tmp_path / "synthetic-large.json"
    with path.open("w", encoding="utf-8") as stream:
        stream.write('{"schema_version":1,"kind":"metadata-extraction","records":[')
        for i in range(4000):
            stream.write(("," if i else "") + json.dumps(record("x" * 8192)))
        stream.write("]}")
    tracemalloc.start()
    with path.open(encoding="utf-8") as stream:
        count = sum(1 for _ in records(stream))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == 4000 and peak < 2_000_000


def test_job_cancel_busy_and_publication_boundary(ledger):
    from prompt_ledger.pipeline_jobs import Jobs

    jobs = Jobs(ledger.cfg, ledger)
    jobs.reserve("b" * 32, "cpu")
    with pytest.raises(LedgerError) as error:
        jobs.reserve("c" * 32, "cuda")
    assert error.value.code == "job_busy"
    assert jobs.cancel("b" * 32)["cancellation_requested"]
    jobs.fail_upload("b" * 32, "cancelled")
    jobs.reserve("c" * 32, "cpu")
    jobs.items["c" * 32]["progress"] = {"phase": "publishing"}
    assert jobs.cancel("c" * 32)["cancellation_requested"] is False
    jobs.fail_upload("c" * 32)


def test_job_path_rejection_does_not_leave_busy_lock(ledger):
    from prompt_ledger.pipeline_jobs import Jobs

    jobs = Jobs(ledger.cfg, ledger)
    with (
        patch("prompt_ledger.pipeline_jobs.contained", side_effect=LedgerError("unsafe_path", "synthetic")),
        pytest.raises(LedgerError),
    ):
        jobs.reserve("e" * 32, "cpu")
    assert not jobs.active.locked()
    jobs.reserve("f" * 32, "cpu")
    jobs.fail_upload("f" * 32)


def test_cli_stage_progress_keeps_stdout_machine_json_and_no_text(ledger, capsys):
    from prompt_ledger.cli import main

    source(ledger, document([record()]))
    with patch("prompt_ledger.cli.Ledger", return_value=ledger):
        code = main(
            ["--root", str(ledger.cfg.root), "--device", "cpu", "compare-json", "metadata.json", "--progress"]
        )
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out)["processed"] == 1
    assert captured.err.strip()
    assert all("positive" not in json.loads(line) for line in captured.err.splitlines())
    assert str(ledger.cfg.root) not in captured.out + captured.err


def test_job_accepted_cancel_before_publication_never_publishes(ledger):
    from prompt_ledger.pipeline_jobs import Jobs

    jobs = Jobs(ledger.cfg, ledger)
    identifier = "d" * 32
    jobs.reserve(identifier, "cpu")

    def fake_compare(*args, cancel, progress, **kwargs):
        assert jobs.cancel(identifier)["cancellation_requested"]
        progress({"phase": "publishing"})
        raise AssertionError("publication must be blocked")

    with patch("prompt_ledger.pipeline_jobs.compare_json", side_effect=fake_compare):
        jobs.start(identifier)
        for _ in range(200):
            if jobs.status(identifier)["state"] in ("cancelled", "failed"):
                break
            time.sleep(0.005)
    assert jobs.status(identifier)["state"] == "cancelled"
    assert not jobs.active.locked()


def test_binary_renamed_json_is_not_retained_by_upload_or_snapshot(ledger):
    raw = b"\x89PNG\r\n\x1a\n" + b"synthetic-not-an-image"
    path = ledger.cfg.path("output_dir") / "renamed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    with pytest.raises(LedgerError):
        compare_json(ledger, path)
    assert not list(ledger.cfg.path("data_dir").glob(".compare-source-*"))
    app = create_app(ledger.cfg, ledger)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Ledger-Token": client.get("/session").json()["token"]}
        response = client.post("/api/compare-json?job_id=" + "0" * 32, content=raw, headers=headers)
        assert response.status_code == 400
        assert not list((path.parent / "staged" / ("0" * 32)).iterdir())
        assert not app.state.pipeline_jobs.active.locked()


@pytest.mark.parametrize("prefix", ["\u00a0", "\ufeff"])
def test_non_json_whitespace_is_rejected(prefix):
    with pytest.raises(LedgerError):
        list(records(io.StringIO(prefix + document([]))))


def test_api_cancel_wakes_stalled_upload_and_releases_after_cleanup(ledger):
    import asyncio

    async def exercise():
        app = create_app(ledger.cfg, ledger)
        jobs = app.state.pipeline_jobs
        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/compare-json")
        waiting, interrupted = asyncio.Event(), asyncio.Event()

        class Request:
            async def stream(self):
                yield b'{"schema_version":1,"kind":"metadata-extraction","records":['
                waiting.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    interrupted.set()

        identifier = "9" * 32
        task = asyncio.create_task(endpoint(Request(), job_id=identifier, device="cpu", name="metadata.json"))
        await asyncio.wait_for(waiting.wait(), 2)
        assert jobs.status(identifier)["state"] == "uploading"
        assert jobs.active.locked()
        ack = await asyncio.to_thread(jobs.cancel, identifier)
        assert ack["cancellation_requested"]
        with pytest.raises(LedgerError) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.code == "cancelled"
        assert interrupted.is_set()
        assert jobs.status(identifier)["state"] == "cancelled"
        assert jobs.items[identifier]["upload_cancel"] is None
        assert not jobs.active.locked()
        root = ledger.cfg.path("output_dir") / f"staged/{identifier}"
        assert not (root / "source.part").exists() and not (root / "source.json").exists()
        assert ledger.records()["records"] == []
        jobs.reserve("8" * 32, "cpu")
        jobs.fail_upload("8" * 32)

    asyncio.run(exercise())


def test_cancel_before_upload_callback_registration_is_not_lost(ledger):
    from prompt_ledger.pipeline_jobs import Jobs

    jobs = Jobs(ledger.cfg, ledger)
    identifier = "7" * 32
    jobs.reserve(identifier, "cpu")
    jobs.cancel(identifier)
    called = []
    jobs.bind_upload_cancel(identifier, lambda: called.append(True))
    assert called == [True]
    assert "upload_cancel" not in jobs.status(identifier)
    jobs.bind_upload_cancel(identifier, None)
    jobs.fail_upload(identifier, "cancelled")
    assert not jobs.active.locked()
