"""Explicit text-JSON upload, private progress and atomic-result download."""

import asyncio
import os

import anyio
from fastapi import Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .config import LedgerError, contained, relative_path
from .json_records import TextGuard
from .pipeline import check_cancel
from .pipeline_jobs import Jobs


def register(app, cfg, ledger):
    jobs = Jobs(cfg, ledger)
    app.state.pipeline_jobs = jobs

    @app.post("/api/compare-json")
    async def upload(request: Request, job_id: str, device: str = "cpu", name: str = "metadata.json"):
        if relative_path(name).suffix.lower() != ".json":
            raise LedgerError("usage", "Choose an extraction JSON file.")
        root = jobs.reserve(job_id, device)
        temporary = root / "source.part"
        try:
            guard = TextGuard()
            loop = asyncio.get_running_loop()
            with anyio.CancelScope() as upload_scope:
                jobs.bind_upload_cancel(job_id, lambda: loop.call_soon_threadsafe(upload_scope.cancel))
                try:
                    check_cancel(jobs.cancelled(job_id))
                    with temporary.open("xb") as stream:
                        async for chunk in request.stream():
                            check_cancel(jobs.cancelled(job_id))
                            guard.feed(chunk)
                            await run_in_threadpool(stream.write, chunk)
                        guard.feed(b"", final=True)
                        check_cancel(jobs.cancelled(job_id))
                        await run_in_threadpool(stream.flush)
                        await run_in_threadpool(os.fsync, stream.fileno())
                finally:
                    jobs.bind_upload_cancel(job_id, None)
            check_cancel(jobs.cancelled(job_id))
            os.replace(temporary, root / "source.json")
            jobs.start(job_id)
        except BaseException as exc:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                jobs.fail_upload(job_id, getattr(exc, "code", "upload_failed"))
            raise
        return {"schema_version": 1, "job_id": job_id}

    @app.get("/api/compare-json/{job_id}")
    def status(job_id: str):
        return {"schema_version": 1, **jobs.status(job_id)}

    @app.post("/api/compare-json/{job_id}/cancel")
    def cancel(job_id: str):
        return {"schema_version": 1, **jobs.cancel(job_id)}

    @app.get("/api/compare-json/{job_id}/result")
    def result(job_id: str):
        if jobs.status(job_id)["state"] != "done":
            raise LedgerError("job_pending", "No complete comparison JSON is available yet.")
        path = contained(cfg.path("output_dir"), f"staged/{job_id}/compared.json")
        return FileResponse(path, media_type="application/json", filename="compared.json")
