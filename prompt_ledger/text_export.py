"""Explicit metadata text export without embeddings or semantic filtering."""

import json
import os
import tempfile

from .config import LedgerError, contained
from .exclusions import matches
from .parsers import extract


def metadata_record(name, raw, terms):
    try:
        parsed = extract(name, raw)
    except LedgerError as exc:
        return {"source": name, "status": "review", "reason": exc.code}
    if matches(parsed.positive, parsed.negative, terms):
        return {"source": name, "status": "excluded", "reason": "exclusion_rule_match"}
    return {"source": name, "status": "extracted", "positive": parsed.positive, "negative": parsed.negative}


def export_text(ledger, files, name):
    target = contained(ledger.cfg.path("output_dir"), name)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".extract-", dir=target.parent)
    counts = {}
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output, ledger.connect() as conn:
            conn.execute("BEGIN")
            version, terms = ledger._rules(conn)
            output.write(
                json.dumps({"schema_version": 1, "kind": "metadata-extraction", "rule_version": version})[:-1]
                + ', "records": ['
            )
            separator = ""
            for source, raw in files:
                record = metadata_record(source, raw, terms)
                counts[record["status"]] = counts.get(record["status"], 0) + 1
                output.write(separator + json.dumps(record, ensure_ascii=False))
                separator = ","
            output.write("]}\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"schema_version": 1, "path": target.relative_to(ledger.cfg.root).as_posix(), "counts": counts}
