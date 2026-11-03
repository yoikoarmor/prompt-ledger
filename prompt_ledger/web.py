import base64
import binascii
import json
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .config import MAX_BATCH_BYTES, MAX_FILES, Config, LedgerError
from .core import Ledger
from .parsers import extract

STATIC = Path(__file__).resolve().parent / "static"
MAX_REQUEST = (MAX_BATCH_BYTES * 4 // 3) + 128 * 1024


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(max_length=14 * 1024 * 1024)


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[FileInput] = Field(min_length=1, max_length=MAX_FILES)
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

        await self.app(scope, replay, secure_send)


def create_app(cfg=None, ledger=None, port=8765):
    cfg = cfg or Config.load()
    ledger = ledger or Ledger(cfg)
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="Prompt Ledger — local metadata API", version="0.1.0", docs_url=None, redoc_url=None)
    app.add_middleware(LocalBoundary, token=token, port=port)

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
        if sum(len(raw) for _, raw in files) > MAX_BATCH_BYTES:
            raise LedgerError("batch_limit", "Total files exceed 20 MiB.")
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

    @app.post("/api/review/{record_id}")
    def resolve(record_id: int, correction: Correction):
        return ledger.resolve(record_id, correction.positive, correction.negative)

    @app.get("/api/export", summary="Explicitly export accepted prompt text (not privacy mode)")
    def export():
        return Response(
            json.dumps(ledger.export(), ensure_ascii=False),
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
