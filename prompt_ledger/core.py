import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from .config import MAX_BATCH_BYTES, MAX_FILES, LedgerError, contained, relative_path
from .embedding import QwenEmbedder, cosine, normalize
from .exclusions import matches, validate_terms
from .parsers import extract, pair


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, cfg, embedder=None):
        self.cfg = cfg
        self.embedder = embedder or QwenEmbedder(cfg)
        cfg.path("data_dir").mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, input_hash TEXT NOT NULL UNIQUE, pair_hash TEXT,
                    source TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                    positive TEXT, negative TEXT, positive_vector TEXT, negative_vector TEXT,
                    nearest_id INTEGER, positive_score REAL, negative_score REAL,
                    created_at TEXT NOT NULL, resolved_by INTEGER
                );
                CREATE INDEX IF NOT EXISTS pairs ON records(pair_hash);
                CREATE TABLE IF NOT EXISTS exclusion_rules (
                    version INTEGER PRIMARY KEY, terms TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exclusion_events (
                    record_id INTEGER PRIMARY KEY, rule_version INTEGER NOT NULL
                );
            """)
            schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if schema and schema[0] != "1":
                raise LedgerError("schema", "Unsupported database schema.")
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
            conn.execute("INSERT OR IGNORE INTO exclusion_rules VALUES (0,'[]',?)", (stamp(),))

    def _rules(self, conn):
        row = conn.execute(
            "SELECT version,terms FROM exclusion_rules ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row["version"], json.loads(row["terms"])

    def rules(self, privacy=True):
        with self.connect() as conn:
            version, terms = self._rules(conn)
        result = {
            "schema_version": 1,
            "version": version,
            "count": len(terms),
            "matching": "literal-substring-unicode-casefold",
            "case_sensitive": False,
        }
        if not privacy:
            result["terms"] = terms
        return result

    def set_rules(self, terms, expected_version):
        terms = validate_terms(terms)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            version, old = self._rules(conn)
            if expected_version != version:
                raise LedgerError("rules_conflict", "Rules changed; reload before saving.")
            if old != terms:
                conn.execute(
                    "INSERT INTO exclusion_rules VALUES (?,?,?)",
                    (version + 1, json.dumps(terms, ensure_ascii=False), stamp()),
                )
                version += 1
        return {"schema_version": 1, "version": version, "count": len(terms)}

    @contextlib.contextmanager
    def connect(self):
        db = contained(self.cfg.path("data_dir"), "ledger.sqlite3")
        conn = sqlite3.connect(db, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _bind_space(self, conn, *, pin=False):
        old = conn.execute("SELECT value FROM meta WHERE key='space'").fetchone()
        if (
            old
            and not conn.execute(
                "SELECT 1 FROM records WHERE status='accepted' OR positive_vector IS NOT NULL "
                "OR negative_vector IS NOT NULL LIMIT 1"
            ).fetchone()
        ):
            # Earlier versions could pin a space for metadata-only review records.
            # With no saved vectors/accepted entries there is no vector space to preserve.
            conn.execute("DELETE FROM meta WHERE key='space'")
            old = None
        if old is None and not pin:
            return
        identity = dict(self.embedder.identity)
        identity["threshold"] = self.cfg.threshold
        identity["rule"] = "positive-and-negative-v1"
        encoded = json.dumps(identity, sort_keys=True)
        if old and old[0] != encoded:
            raise LedgerError(
                "model_mismatch", "Model or decision settings differ from this ledger. Use a new root."
            )
        if pin:
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('space',?)", (encoded,))

    def _public(self, row, privacy=True, terms=()):
        result = {
            k: row[k]
            for k in (
                "id",
                "status",
                "reason",
                "nearest_id",
                "positive_score",
                "negative_score",
                "created_at",
                "resolved_by",
            )
        }
        matched = matches(row["positive"], row["negative"], terms)
        if matched:
            result.update(status="excluded", reason="exclusion_rule_match", stored_status=row["status"])
        if not privacy:
            result.update({k: row[k] for k in ("source", "positive", "negative")})
            result["matched_terms"] = matched
        return result

    def _add(self, conn, source, raw=None, corrected=None):
        relative_path(source)
        # Only metadata hashes are persisted; image bytes are never stored.
        try:
            parsed = corrected if corrected is not None else extract(source, raw)
            digest = parsed.digest
            input_hash = "pair:" + digest
            problem = None
        except LedgerError as exc:
            parsed, digest = None, None
            # Review idempotence includes source and bytes, but not a stored copy.
            input_hash = "review:" + hashlib.sha256(source.encode() + b"\0" + raw).hexdigest()
            problem = exc.code
        version, terms = self._rules(conn)
        blocked = parsed is not None and bool(matches(parsed.positive, parsed.negative, terms))
        if blocked:
            prior = conn.execute("SELECT * FROM records WHERE input_hash=?", (input_hash,)).fetchone()
            if prior:
                result = self._public(prior, terms=terms)
                result["reused"] = True
                return result
            input_hash = "excluded:" + digest
        elif parsed:
            self._bind_space(conn)
        old = conn.execute("SELECT * FROM records WHERE input_hash=?", (input_hash,)).fetchone()
        if old:
            result = self._public(old, terms=terms)
            result["reused"] = True
            return result
        status, reason, nearest, ps, ns, pv, nv = "review", problem, None, None, None, None, None
        if blocked:
            status, reason = "excluded", "excluded_at_import"
        elif parsed:
            dim = self.embedder.identity["dimension"]
            try:
                pv = normalize(self.embedder.encode(parsed.positive), dim)
                nv = normalize(self.embedder.encode(parsed.negative), dim) if parsed.negative else None
            except LedgerError as exc:
                if exc.code != "token_limit":
                    raise
                status, reason = "review", exc.code
                pv, nv = None, None
            else:
                # Both vectors are validated. Pin in the same transaction that saves them.
                self._bind_space(conn, pin=True)
                status, reason = "accepted", "new_pair"
                best = -2.0
                for candidate in conn.execute("SELECT * FROM records WHERE status='accepted' ORDER BY id"):
                    if matches(candidate["positive"], candidate["negative"], terms):
                        continue
                    positive_score = cosine(pv, json.loads(candidate["positive_vector"]))
                    if not parsed.negative and not candidate["negative"]:
                        negative_score = 1.0
                    elif not parsed.negative or not candidate["negative"]:
                        negative_score = 0.0
                    else:
                        negative_score = cosine(nv, json.loads(candidate["negative_vector"]))
                    # Maximize the limiting score of this conjunctive rule.
                    score = min(positive_score, negative_score)
                    if score > best:
                        best, nearest, ps, ns = score, candidate["id"], positive_score, negative_score
                if nearest is not None and ps >= self.cfg.threshold and ns >= self.cfg.threshold:
                    status, reason = "duplicate", "both_cosines_at_or_above_threshold"
        cur = conn.execute(
            """INSERT INTO records
            (input_hash,pair_hash,source,status,reason,positive,negative,positive_vector,negative_vector,
             nearest_id,positive_score,negative_score,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                input_hash,
                digest,
                source,
                status,
                reason,
                parsed.positive if parsed else None,
                parsed.negative if parsed else None,
                json.dumps(pv) if pv else None,
                json.dumps(nv) if nv else None,
                nearest,
                ps,
                ns,
                stamp(),
            ),
        )
        if blocked:
            conn.execute("INSERT INTO exclusion_events VALUES (?,?)", (cur.lastrowid, version))
        return self._public(
            conn.execute("SELECT * FROM records WHERE id=?", (cur.lastrowid,)).fetchone(), terms=terms
        )

    def ingest(self, files, dry_run=False):
        if not files or len(files) > MAX_FILES or sum(len(raw) for _, raw in files) > MAX_BATCH_BYTES:
            raise LedgerError("batch_limit", "Select 1–20 files, at most 20 MiB in total.")
        for source, _ in files:
            relative_path(source)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                results = [self._add(conn, name, raw) for name, raw in files]
                conn.rollback() if dry_run else conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"schema_version": 1, "dry_run": dry_run, "results": results}

    def resolve(self, record_id, positive, negative):
        corrected = pair(positive, negative)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM records WHERE id=? AND status='review'", (record_id,)
                ).fetchone()
                if not row:
                    raise LedgerError("not_found", "Review record does not exist.")
                if row["resolved_by"] is not None:
                    raise LedgerError(
                        "already_resolved", "This review has already been resolved; its history is preserved."
                    )
                result = self._add(conn, row["source"], corrected=corrected)
                target = conn.execute("SELECT status FROM records WHERE id=?", (result["id"],)).fetchone()
                # Effective exclusion is a display/filter state, never proof of resolution.
                # Keep excluded corrections pending so they can be retried after rule edits.
                if result["id"] != record_id and target["status"] in ("accepted", "duplicate"):
                    conn.execute("UPDATE records SET resolved_by=? WHERE id=?", (result["id"], record_id))
                conn.commit()
                return {"schema_version": 1, "result": result}
            except Exception:
                conn.rollback()
                raise

    def records(self, privacy=True, limit=100, offset=0):
        with self.connect() as conn:
            conn.execute("BEGIN")
            _, terms = self._rules(conn)
            rows = conn.execute("SELECT * FROM records ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
            result = [self._public(r, privacy, terms) for r in rows]
            if not privacy:
                for record in result:
                    event = conn.execute(
                        "SELECT e.rule_version,r.terms FROM exclusion_events e JOIN exclusion_rules r "
                        "ON r.version=e.rule_version WHERE e.record_id=?",
                        (record["id"],),
                    ).fetchone()
                    if event:
                        record["excluded_at_rule_version"] = event["rule_version"]
                        record["original_matched_terms"] = matches(
                            record["positive"], record["negative"], json.loads(event["terms"])
                        )
            return {"schema_version": 1, "records": result}

    def status(self):
        with self.connect() as conn:
            conn.execute("BEGIN")
            _, terms = self._rules(conn)
            counts = {s: 0 for s in ("accepted", "duplicate", "review")}
            for row in conn.execute("SELECT * FROM records"):
                status = self._public(row, terms=terms)["status"]
                counts[status] = counts.get(status, 0) + 1
            pending = conn.execute(
                "SELECT count(*) FROM records WHERE status='review' AND resolved_by IS NULL"
            ).fetchone()[0]
            space = conn.execute("SELECT value FROM meta WHERE key='space'").fetchone()
        try:
            model, model_state = self.embedder.identity, "configured"
        except (LedgerError, OSError, ValueError):
            model, model_state = None, "needs_setup"
        return {
            "schema_version": 1,
            "counts": counts,
            "pending_review": pending,
            "model": model,
            "model_state": model_state,
            "threshold": self.cfg.threshold,
            "space": json.loads(space[0]) if space else None,
        }

    def export(self):
        with self.connect() as conn:
            conn.execute("BEGIN")
            _, terms = self._rules(conn)
            space = conn.execute("SELECT value FROM meta WHERE key='space'").fetchone()
            rows = conn.execute(
                "SELECT id,positive,negative,source,pair_hash FROM records WHERE status='accepted' ORDER BY id"
            )
            return {
                "schema_version": 1,
                "space": json.loads(space[0]) if space else None,
                "records": [
                    dict(row) for row in rows if not matches(row["positive"], row["negative"], terms)
                ],
            }

    def export_file(self, name="accepted.json"):
        target = contained(self.cfg.path("output_dir"), name)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.export()
        fd, temporary = tempfile.mkstemp(prefix=".export-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "schema_version": 1,
            "path": target.relative_to(self.cfg.root).as_posix(),
            "count": len(payload["records"]),
        }
