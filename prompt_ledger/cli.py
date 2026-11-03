import argparse
import contextlib
import json
import sys
from dataclasses import replace

from .config import Config, LedgerError, contained
from .core import Ledger
from .embedding import setup_model
from .inputs import read_inputs
from .parsers import extract
from .pipeline import compare_json
from .text_export import export_text


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError("usage", message)


def parser():
    p = Parser(description="Prompt Ledger: metadata-only local prompt deduplication")
    p.add_argument("--root", help="Project root; overrides LEDGER_ROOT. Paths inside it stay relative.")
    p.add_argument("--device", choices=("cpu", "cuda", "auto"), help="Runtime-only device override")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    sub.add_parser("status")
    rules = sub.add_parser("rules", help="Persistent literal exclusion rules")
    rules.add_argument("--include-text", action="store_true")
    rules.add_argument("--set-json", help="Replace with a JSON array of literal strings; [] clears")
    rules.add_argument("--expected-version", type=int)
    records = sub.add_parser("records")
    records.add_argument("--include-text", action="store_true")
    records.add_argument("--offset", type=int, default=0)
    records.add_argument("--limit", type=int, default=100)
    load = sub.add_parser("import", aliases=["batch"])
    load.add_argument("paths", nargs="+", help="File or explicitly selected directory relative to input_dir")
    load.add_argument(
        "--recursive",
        action="store_true",
        help="Opt in to nested folders of the explicitly selected directory",
    )
    load.add_argument("--dry-run", action="store_true")
    load.add_argument("--check-only", action="store_true", help="Parse only; no model or database writes")
    text = sub.add_parser("extract", help="Export metadata text without embeddings; duplicates retained")
    text.add_argument("paths", nargs="+")
    text.add_argument("--recursive", action="store_true")
    text.add_argument("--output", default="metadata.json")
    staged = sub.add_parser(
        "compare-json", help="Compare an extraction JSON and atomically write a different accepted JSON"
    )
    staged.add_argument("path", help="Extraction JSON relative to output_dir by default")
    staged.add_argument("--from-input", action="store_true")
    staged.add_argument("--output", default="compared.json")
    staged.add_argument("--batch-size", type=int, default=8)
    staged.add_argument(
        "--progress",
        action="store_true",
        help="Progress JSON to stderr; stdout remains the final machine result",
    )
    export = sub.add_parser("export")
    export.add_argument("--output", default="accepted.json", help="Relative to output_dir")
    model = sub.add_parser("setup-model")
    model.add_argument("--revision", required=True, help="Official immutable model commit SHA")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    return p


def execute(args):
    cfg = Config.load(args.root)
    if args.device:
        cfg = replace(cfg, device=args.device)
    if args.command in ("import", "batch"):
        files = read_inputs(cfg, args.paths, args.recursive)
        if args.check_only:
            results, counts, processed = [], {}, 0
            for name, raw in files:
                try:
                    extract(name, raw)
                    result = {"status": "parsed"}
                except LedgerError as exc:
                    result = {"status": "review", "reason": exc.code}
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                processed += 1
                if len(results) < 200:
                    results.append(result)
            return {
                "schema_version": 1,
                "check_only": True,
                "results": results,
                "processed": processed,
                "counts": counts,
                "results_truncated": processed > len(results),
            }
        return Ledger(cfg).ingest(files, args.dry_run)
    if args.command == "extract":
        return export_text(Ledger(cfg), read_inputs(cfg, args.paths, args.recursive), args.output)
    if args.command == "compare-json":
        source = contained(cfg.path("input_dir" if args.from_input else "output_dir"), args.path)
        progress = (
            (lambda value: print(json.dumps(value, ensure_ascii=True), file=sys.stderr, flush=True))
            if args.progress
            else None
        )
        return compare_json(Ledger(cfg), source, args.output, batch_size=args.batch_size, progress=progress)
    if args.command == "setup-model":
        return {"schema_version": 1, "model": setup_model(cfg, args.revision)}
    if args.command == "serve":
        if not 1024 <= args.port <= 65535:
            raise LedgerError("port", "Port must be between 1024 and 65535.")
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(cfg, port=args.port), host="127.0.0.1", port=args.port, access_log=False)
        return {"schema_version": 1, "status": "stopped"}
    ledger = Ledger(cfg)
    if args.command == "rules":
        if args.set_json is not None:
            if args.expected_version is None:
                raise LedgerError("usage", "--expected-version is required for edits.")
            return ledger.set_rules(json.loads(args.set_json), args.expected_version)
        return ledger.rules(privacy=not args.include_text)
    if args.command == "status":
        return ledger.status()
    if args.command == "records":
        if not 1 <= args.limit <= 200 or args.offset < 0:
            raise LedgerError("pagination", "Limit must be 1–200 and offset nonnegative.")
        return ledger.records(not args.include_text, args.limit, args.offset)
    if args.command == "export":
        return ledger.export_file(args.output)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        # Third-party progress/log output must never corrupt JSON stdout.
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(args)
        code = (
            3
            if (
                result.get("counts", {}).get("review", 0)
                or any(r["status"] == "review" for r in result.get("results", []))
            )
            else 0
        )
    except KeyboardInterrupt:
        result = {
            "schema_version": 1,
            "error": {
                "code": "cancelled",
                "message": "Cancelled; current transaction rolled back; completed stages or batches may remain.",
            },
        }
        code = 130
    except LedgerError as exc:
        result = {"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}}
        code = 2
    except Exception as exc:  # noqa: BLE001 - top-level machine protocol must always emit JSON
        # Do not put machine-specific filesystem paths or input content into error output.
        result = {"schema_version": 1, "error": {"code": "runtime", "message": type(exc).__name__}}
        code = 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
