"""Persistent, restartable-by-replay staged comparison; bounded text and vector batches."""

import contextlib
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path

from .config import LedgerError, contained, relative_path
from .exclusions import matches
from .json_records import TextGuard, records
from .parsers import pair


def check_cancel(event):
    if event.is_set():
        raise LedgerError("cancelled", "Cancelled; completed batches remain, current batch rolls back.")


def compare_json(ledger, source, output="compared.json", *, cancel=None, progress=None, batch_size=8):
    if not 1 <= batch_size <= 16:
        raise LedgerError("usage", "Batch size must be between 1 and 16.")
    cancel = cancel or threading.Event()
    progress = progress or (lambda value: None)
    source = Path(source)
    target = contained(ledger.cfg.path("output_dir"), output)
    if source.resolve() == target.resolve():
        raise LedgerError("usage", "Source JSON and comparison output must be different files.")
    started = time.perf_counter()
    comparison_state = {"backend": "not_run", "fallback_reason": None}
    stats = {
        "processed": 0,
        "reused": 0,
        "counts": {},
        "batch_size": batch_size,
        "timings": {
            "snapshot_seconds": 0.0,
            "parse_seconds": 0.0,
            "embedding_seconds": 0.0,
            "comparison_seconds": 0.0,
            "normalization_seconds": 0.0,
        },
    }

    def notify(phase):
        check_cancel(cancel)
        progress(
            {
                "phase": phase,
                **stats,
                "execution": ledger.embedder.execution() if hasattr(ledger.embedder, "execution") else None,
                "comparison": dict(comparison_state),
            }
        )

    fd, snapshot = tempfile.mkstemp(prefix=".compare-source-", dir=ledger.cfg.path("data_dir"))
    try:
        digest = hashlib.sha256()
        guard = TextGuard()
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            for chunk in iter(lambda: src.read(65536), b""):
                check_cancel(cancel)
                guard.feed(chunk)
                digest.update(chunk)
                dst.write(chunk)
            guard.feed(b"", final=True)
        stats["input_sha256"] = digest.hexdigest()
        stats["timings"]["snapshot_seconds"] = time.perf_counter() - started
        with ledger.connect() as conn:
            rule_version, _ = ledger._rules(conn)
        stats["run_rule_version"] = rule_version
        notify("parsing")
        with open(snapshot, encoding="utf-8") as stream, ledger.connect() as conn:
            iterator = iter(records(stream))
            exhausted = False
            while not exhausted:
                check_cancel(cancel)
                chunk = []
                before = time.perf_counter()
                for _ in range(batch_size):
                    try:
                        record = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    if record.get("status") not in ("extracted", "excluded", "review"):
                        raise LedgerError("stage_json", "Unsupported extraction record status.")
                    relative_path(record.get("source"))
                    if record["status"] == "extracted":
                        pair(record.get("positive"), record.get("negative"))
                    chunk.append(record)
                stats["timings"]["parse_seconds"] += time.perf_counter() - before
                if not chunk:
                    break
                # DB commit, including both vectors and all decisions, is one bounded batch.
                conn.execute("BEGIN IMMEDIATE")
                current_version, terms = ledger._rules(conn)
                if current_version != rule_version:
                    raise LedgerError(
                        "rules_conflict", "Rules changed; replay the source JSON with current rules."
                    )
                pending = {}
                for record in chunk:
                    if record["status"] != "extracted":
                        continue
                    parsed = pair(record["positive"], record["negative"])
                    if matches(parsed.positive, parsed.negative, terms):
                        continue
                    ledger._bind_space(conn)
                    if not conn.execute(
                        "SELECT 1 FROM records WHERE input_hash=?", ("pair:" + parsed.digest,)
                    ).fetchone():
                        pending[parsed.digest] = parsed
                prepared = {}
                if pending and hasattr(ledger.embedder, "encode_texts"):
                    texts = [
                        text
                        for parsed in pending.values()
                        for text in (
                            [parsed.positive, parsed.negative] if parsed.negative else [parsed.positive]
                        )
                    ]
                    before = time.perf_counter()
                    notify("embedding")
                    try:
                        vectors = iter(ledger.embedder.encode_texts(texts))
                        for key, parsed in pending.items():
                            prepared[key] = (next(vectors), next(vectors) if parsed.negative else None)
                    except LedgerError as exc:
                        if exc.code != "token_limit":
                            raise
                        # A long record must go to review without suppressing other records.
                        prepared.clear()
                    stats["timings"]["embedding_seconds"] += time.perf_counter() - before
                batch_results = []
                notify("comparing")
                for record in chunk:
                    check_cancel(cancel)
                    if record["status"] != "extracted":
                        batch_results.append({"status": record["status"]})
                        continue
                    parsed = pair(record["positive"], record["negative"])
                    batch_results.append(
                        ledger._add(
                            conn,
                            record["source"],
                            corrected=parsed,
                            prepared_vectors=prepared.get(parsed.digest),
                            timings=stats["timings"],
                            comparison_diagnostics=comparison_state,
                        )
                    )
                check_cancel(cancel)
                conn.commit()
                for result in batch_results:
                    stats["processed"] += 1
                    stats["counts"][result["status"]] = stats["counts"].get(result["status"], 0) + 1
                    stats["reused"] += int(result.get("reused", False))
                notify("comparing")
        stats["timings"]["processing_seconds"] = time.perf_counter() - started
        stats["execution"] = ledger.embedder.execution() if hasattr(ledger.embedder, "execution") else None
        stats["comparison_backend"] = dict(comparison_state)
        notify("exporting")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".compare-output-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                with contextlib.closing(ledger.export_chunks(comparison=stats)) as chunks:
                    for text in chunks:
                        check_cancel(cancel)
                        out.write(text)
                out.flush()
                os.fsync(out.fileno())
            check_cancel(cancel)
            progress({"phase": "publishing", **stats})
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        stats["timings"]["total_seconds"] = time.perf_counter() - started
        return {
            "schema_version": 1,
            "path": target.relative_to(ledger.cfg.root).as_posix(),
            **stats,
            "execution": ledger.embedder.execution() if hasattr(ledger.embedder, "execution") else None,
            "comparison": dict(comparison_state),
        }
    finally:
        os.unlink(snapshot)
