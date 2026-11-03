import base64
import binascii
import secrets
import time
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .batches import DryBatch
from .config import MAX_BATCH_BYTES, Config, LedgerError
from .core import Ledger
from .parsers import extract
from .pipeline_web import register as register_pipeline
from .streaming import MetadataStream
from .text_export import metadata_record


class ClosingStreamingResponse(StreamingResponse):
    """Own the iterator through send failures, disconnects and cancellation."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Disconnect cancellation must not interrupt async-generator finalization.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


STATIC = Path(__file__).resolve().parent / "static"
MAX_REQUEST = (MAX_BATCH_BYTES * 4 // 3) + 128 * 1024


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=512)
    content_base64: str


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[FileInput] = Field(min_length=1)
    dry_run: bool = False
    check_only: bool = False


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positive: str = Field(min_length=1, max_length=128 * 1024)
    negative: str = Field(max_length=128 * 1024)


class ExclusionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terms: list[str] = Field(max_length=100)
    expected_version: int = Field(ge=0)


class LocalBoundary:
    """Protect loopback against cross-origin requests, DNS rebinding and oversized bodies."""

    def __init__(self, app, token, port):
        self.app, self.token, self.port = app, token, port

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = f"127.0.0.1:{self.port}"
        origin = headers.get("origin")
        failure = None
        if headers.get("host") != host or (origin and origin != f"http://{host}"):
            failure = (403, "Only the exact loopback origin is allowed.")
        if headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            failure = (403, "Cross-site access is not allowed.")
        protected = scope["path"].startswith("/api/")
        if protected and not secrets.compare_digest(headers.get("x-ledger-token", ""), self.token):
            failure = (403, "A local session token is required.")
        if failure:
            return await JSONResponse(
                {"schema_version": 1, "error": {"code": "forbidden", "message": failure[1]}},
                status_code=failure[0],
            )(scope, receive, send)

        async def secure_send(event):
            if event["type"] == "http.response.start":
                event["headers"] += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(event)

        if scope["path"] in ("/api/file", "/api/compare-json") and scope["method"] == "POST":
            return await self.app(scope, receive, secure_send)

        # Bound chunked bodies before FastAPI/JSON parsing; Content-Length is untrusted.
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > MAX_REQUEST:
                return await JSONResponse(
                    {"schema_version": 1, "error": {"code": "body_limit"}}, status_code=413
                )(scope, receive, send)
            if not event.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, secure_send)


def create_app(cfg=None, ledger=None, port=8765):
    cfg = cfg or Config.load()
    ledger = ledger or Ledger(cfg)
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="Prompt Ledger — local metadata API", version="0.1.0", docs_url=None, redoc_url=None)
    app.add_middleware(LocalBoundary, token=token, port=port)
    register_pipeline(app, cfg, ledger)

    @app.exception_handler(LedgerError)
    async def ledger_error(request, exc):
        return JSONResponse(
            {"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}},
            status_code=409 if exc.code in ("already_resolved", "rules_conflict") else 400,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo submitted metadata or base64 in validation errors.
        return JSONResponse(
            {"schema_version": 1, "error": {"code": "validation", "message": "Invalid request schema."}},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def runtime_error(request, exc):
        return JSONResponse(
            {
                "schema_version": 1,
                "error": {"code": "runtime", "message": "Local processing failed; check local diagnostics."},
            },
            status_code=500,
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    def style():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @app.get("/session", summary="Obtain an ephemeral local token (same origin only)")
    def session():
        return {"schema_version": 1, "token": token}

    @app.get("/api/status")
    def status():
        return ledger.status()

    @app.get("/api/rules")
    def rules(privacy: bool = True):
        return ledger.rules(privacy)

    @app.put("/api/rules")
    def update_rules(body: ExclusionRules):
        return ledger.set_rules(body.terms, body.expected_version)

    @app.get("/api/records")
    def records(privacy: bool = True, limit: int = 100, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise LedgerError("pagination", "Limit must be 1–200 and offset nonnegative.")
        return ledger.records(privacy, limit, offset)

    @app.post("/api/import")
    def ingest(batch: BatchInput):
        try:
            files = [(f.name, base64.b64decode(f.content_base64, validate=True)) for f in batch.files]
        except (ValueError, binascii.Error):
            raise LedgerError("base64", "Invalid base64 file content.") from None
        if batch.check_only:
            results = []
            for name, raw in files:
                try:
                    extract(name, raw)
                    results.append({"status": "parsed"})
                except LedgerError as exc:
                    results.append({"status": "review", "reason": exc.code})
            return {"schema_version": 1, "check_only": True, "results": results}
        return ledger.ingest(files, batch.dry_run)

    dry_batches = {}

    @app.post("/api/dry-batch")
    async def begin_dry_batch():
        if any(not job.finished.is_set() for job in dry_batches.values()):
            raise LedgerError("batch_busy", "Another dry-run is active.")
        dry_batches.clear()
        batch_id = secrets.token_urlsafe(24)
        dry_batches[batch_id] = DryBatch(ledger)
        return {"schema_version": 1, "batch_id": batch_id}

    @app.delete("/api/dry-batch/{batch_id}")
    async def end_dry_batch(batch_id: str):
        job = dry_batches.get(batch_id)
        complete = True if job is None else await run_in_threadpool(job.close)
        if complete:
            dry_batches.pop(batch_id, None)
        return {"schema_version": 1, "rolled_back": complete, "cancellation_requested": True}

    @app.post("/api/file", summary="Stream one file without a file-size cap; no disk image copy")
    async def stream_file(
        request: Request,
        name: str,
        check_only: bool = False,
        batch_id: str | None = None,
        extract_only: bool = False,
    ):
        if extract_only and (batch_id or check_only):
            raise LedgerError("usage", "Extraction is separate from check-only or dry-run.")
        stream = MetadataStream(name)
        async for chunk in request.stream():
            if batch_id and batch_id in dry_batches:
                dry_batches[batch_id].last_activity = time.monotonic()
            stream.feed(chunk)
        raw = stream.finish()
        if extract_only:
            rules = ledger.rules(privacy=False)
            return {
                "schema_version": 1,
                "record": metadata_record(name, raw, rules["terms"]),
                "rule_version": rules["version"],
            }
        if check_only:
            try:
                extract(name, raw)
                result = {"status": "parsed"}
            except LedgerError as exc:
                result = {"status": "review", "reason": exc.code}
            return {"schema_version": 1, "results": [result]}
        if batch_id:
            job = dry_batches.get(batch_id)
            if job is None:
                raise LedgerError("batch_closed", "Dry-run expired; restart the selection.")
            result = await run_in_threadpool(job.submit, name, raw)
            return {"schema_version": 1, "dry_run": True, "results": [result]}
        return await run_in_threadpool(ledger.ingest, [(name, raw)])

    @app.post("/api/review/{record_id}")
    def resolve(record_id: int, correction: Correction):
        return ledger.resolve(record_id, correction.positive, correction.negative)

    @app.get("/api/export", summary="Explicitly export accepted prompt text (not privacy mode)")
    def export():
        async def chunks():
            # SQLite iterator stays on this event-loop thread, including cleanup.
            iterator = ledger.export_chunks()
            try:
                for chunk in iterator:
                    yield chunk.encode("utf-8")
            finally:
                iterator.close()

        return ClosingStreamingResponse(
            chunks(),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="accepted.json"'},
        )

    default_openapi = app.openapi

    def openapi_with_token():
        schema = default_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["LocalSessionToken"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Ledger-Token",
            "description": "Obtain an ephemeral token from GET /session on this same loopback origin.",
        }
        for path, operations in schema["paths"].items():
            if path.startswith("/api/"):
                for operation in operations.values():
                    operation["security"] = [{"LocalSessionToken": []}]
        return schema

    app.openapi = openapi_with_token

    return app
