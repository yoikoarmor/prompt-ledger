import argparse
import contextlib
import json
import sys

from .config import MAX_BATCH_BYTES, MAX_FILE, MAX_FILES, Config, LedgerError, contained
from .core import Ledger
from .embedding import setup_model
from .parsers import PARSERS, extract


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError("usage", message)


def parser():
    p = Parser(description="Prompt Ledger: metadata-only local prompt deduplication")
    p.add_argument("--root", help="Project root; overrides LEDGER_ROOT. Paths inside it stay relative.")
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
    export = sub.add_parser("export")
    export.add_argument("--output", default="accepted.json", help="Relative to output_dir")
    model = sub.add_parser("setup-model")
    model.add_argument("--revision", required=True, help="Official immutable model commit SHA")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    return p


def read_inputs(cfg, paths, recursive):
    root = cfg.path("input_dir")
    selected = []
    for name in paths:
        target = contained(root, name)
        if target.is_dir():
            iterator = target.rglob("*") if recursive else target.iterdir()
            expanded = []
            for child in iterator:
                rel = child.relative_to(root).as_posix()
                contained(root, rel)
                if child.is_file() and child.suffix.lower() in PARSERS:
                    expanded.append((rel, child))
                    if len(selected) + len(expanded) > MAX_FILES:
                        raise LedgerError("batch_limit", "Select at most 20 files per batch.")
            selected.extend(sorted(expanded))
        else:
            selected.append((name, target))
    if not selected or len(selected) > MAX_FILES:
        raise LedgerError("batch_limit", "Select 1–20 files.")
    files, total = [], 0
    for name, path in selected:
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise LedgerError("file_limit", "Input must be a regular file of at most 10 MiB.")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
        total += len(raw)
        if len(raw) > MAX_FILE or total > MAX_BATCH_BYTES:
            raise LedgerError("batch_limit", "Batch size limit exceeded.")
        files.append((name, raw))
    return files


def execute(args):
    cfg = Config.load(args.root)
    if args.command in ("import", "batch"):
        files = read_inputs(cfg, args.paths, args.recursive)
        if args.check_only:
            results = []
            for name, raw in files:
                try:
                    extract(name, raw)
                    results.append({"status": "parsed"})
                except LedgerError as exc:
                    results.append({"status": "review", "reason": exc.code})
            return {"schema_version": 1, "check_only": True, "results": results}
        return Ledger(cfg).ingest(files, args.dry_run)
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
        code = 3 if any(r["status"] == "review" for r in result.get("results", [])) else 0
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
