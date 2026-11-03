import json
import tracemalloc
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from conftest import payload


def test_stream_format_and_atomic_count(ledger):
    ledger.ingest([("a.json", payload("base")), ("b.json", payload("opposite"))])
    expected = ledger.export()
    with patch.object(ledger, "export", side_effect=AssertionError("materialized")):
        result = ledger.export_file()
    target = ledger.cfg.root / result["path"]
    assert json.loads(target.read_text(encoding="utf-8")) == expected
    assert result["count"] == 2


def test_cancel_closes_snapshot_and_preserves_old_file(ledger):
    ledger.ingest([("a.json", payload("base"))])
    original = ledger.connect
    opened = []

    @contextmanager
    def tracked():
        with original() as conn:
            opened.append(conn)
            yield conn

    with patch.object(ledger, "connect", tracked):
        chunks = ledger.export_chunks()
        next(chunks)
        chunks.close()
    with pytest.raises(Exception, match="closed"):
        opened[0].execute("SELECT 1")
    target = ledger.cfg.path("output_dir") / "accepted.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("previous")

    def interrupted():
        yield "partial"
        raise KeyboardInterrupt

    with patch.object(ledger, "export_chunks", interrupted), pytest.raises(KeyboardInterrupt):
        ledger.export_file()
    assert target.read_text() == "previous"
    assert list(target.parent.glob(".export-*")) == []


def test_large_export_bounded_python_memory(ledger):
    # 16 MiB synthetic text, inserted without model calls or real prompt data.
    with ledger.connect() as conn:
        conn.executemany(
            "INSERT INTO records(input_hash,source,status,reason,positive,negative,created_at) "
            "VALUES (?,?,'accepted','new',?,'','synthetic')",
            ((str(i), "synthetic.json", "x" * 8192) for i in range(2000)),
        )
    tracemalloc.start()
    size = sum(len(chunk) for chunk in ledger.export_chunks())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert size > 16_000_000
    assert peak < 2_000_000


def test_stream_rules_snapshot(ledger):
    ledger.ingest([("a.json", payload("base")), ("b.json", payload("opposite"))])
    with ledger.connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    chunks = ledger.export_chunks()
    first = next(chunks)
    ledger.set_rules(["base"], 0)
    assert len(json.loads(first + "".join(chunks))["records"]) == 2
    assert len(ledger.export()["records"]) == 1


@pytest.mark.parametrize("failure", ["disconnect23", "send24", "exception24"])
def test_asgi_disconnect_releases_snapshot_without_gc(ledger, failure):
    import asyncio
    import sqlite3

    from starlette.requests import ClientDisconnect

    from prompt_ledger.web import create_app

    ledger.ingest([("synthetic.json", payload("base"))])
    app = create_app(ledger.cfg, ledger)
    response = next(route.endpoint for route in app.routes if route.path == "/api/export")()

    async def exercise():
        started = asyncio.Event()

        async def receive():
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                started.set()
                if failure == "send24":
                    raise OSError("synthetic disconnect")
                if failure == "exception24":
                    raise ValueError("synthetic send failure")
                await asyncio.Event().wait()  # cancelled by the disconnect listener

        scope = {"type": "http", "asgi": {"spec_version": "2.3" if failure == "disconnect23" else "2.4"}}
        if failure == "disconnect23":
            await response(scope, receive, send)
        else:
            with pytest.raises(ClientDisconnect if failure == "send24" else ValueError):
                await response(scope, receive, send)
        # Retain response and iterator: success must not depend on GC/finalizers.
        assert response.body_iterator is not None
        with sqlite3.connect(ledger.cfg.path("data_dir") / "ledger.sqlite3", timeout=0.05) as writer:
            writer.execute("UPDATE records SET reason='synthetic' WHERE id=1")
            writer.commit()

    asyncio.run(exercise())
