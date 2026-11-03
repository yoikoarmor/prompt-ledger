"""One explicit local comparison at a time; no user data in progress responses."""

import json
import re
import threading
from dataclasses import replace

from .config import LedgerError, contained
from .core import Ledger
from .pipeline import check_cancel, compare_json


class Jobs:
    def __init__(self, cfg, ledger):
        self.cfg, self.ledger = cfg, ledger
        self.lock, self.active = threading.Lock(), threading.Lock()
        self.items, self.ledgers = {}, {cfg.device: ledger}

    def reserve(self, identifier, device):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier) or device not in ("cpu", "cuda", "auto"):
            raise LedgerError("usage", "Invalid job identifier or device.")
        with self.lock:
            if identifier in self.items:
                raise LedgerError("job_exists", "Job identifier already exists; query its status.")
            if not self.active.acquire(blocking=False):
                raise LedgerError("job_busy", "A JSON comparison or upload is already active.")
            # Never overwrite a retained source/result from an earlier process.
            try:
                root = contained(self.cfg.path("output_dir"), f"staged/{identifier}")
                if root.exists():
                    raise LedgerError("job_exists", "Job artifacts already exist; use a new identifier.")
                root.mkdir(parents=True)
            except BaseException:
                self.active.release()
                raise
            while len(self.items) >= 32:
                self.items.pop(next(iter(self.items)))
            self.items[identifier] = {
                "state": "uploading",
                "device": device,
                "event": threading.Event(),
                "upload_cancel": None,
                "progress": {},
            }
        return root

    def status(self, identifier):
        with self.lock:
            if identifier not in self.items:
                raise LedgerError("not_found", "Job not found; the original JSON can be replayed.")
            return json.loads(
                json.dumps(
                    {k: v for k, v in self.items[identifier].items() if k not in ("event", "upload_cancel")}
                )
            )

    def bind_upload_cancel(self, identifier, callback):
        with self.lock:
            job = self.items[identifier]
            job["upload_cancel"] = callback
            cancelled = job["event"].is_set()
        if callback is not None and cancelled:
            callback()

    def cancelled(self, identifier):
        return self.items[identifier]["event"]

    def cancel(self, identifier):
        with self.lock:
            if identifier not in self.items:
                raise LedgerError("not_found", "Job not found.")
            job = self.items[identifier]
            if (
                job["state"] in ("done", "failed", "cancelled")
                or job["progress"].get("phase") == "publishing"
            ):
                return {"cancellation_requested": False, "state": job["state"]}
            job["event"].set()
            upload_cancel = job["upload_cancel"]
        if upload_cancel is not None:
            upload_cancel()
        return {"cancellation_requested": True}

    def fail_upload(self, identifier, code="upload_failed"):
        with self.lock:
            self.items[identifier].update(state="cancelled" if code == "cancelled" else "failed", error=code)
        self.active.release()

    def start(self, identifier):
        def work():
            job = self.items[identifier]
            ledger = None
            try:
                device = job["device"]
                if device not in self.ledgers:
                    self.ledgers[device] = Ledger(replace(self.cfg, device=device))
                ledger = self.ledgers[device]

                def progress(value):
                    with self.lock:
                        if value.get("phase") == "publishing":
                            check_cancel(job["event"])
                        job.update(state="running", progress=json.loads(json.dumps(value)))

                result = compare_json(
                    ledger,
                    contained(self.cfg.path("output_dir"), f"staged/{identifier}/source.json"),
                    f"staged/{identifier}/compared.json",
                    cancel=job["event"],
                    progress=progress,
                )
                with self.lock:
                    job.update(state="done", result=result)
            except BaseException as exc:  # noqa: BLE001 - worker boundary never exposes input or exception text
                code = getattr(exc, "code", "comparison_failed")
                with self.lock:
                    job.update(state="cancelled" if code == "cancelled" else "failed", error=code)
                    if ledger is not None and hasattr(ledger.embedder, "execution"):
                        job["progress"]["execution"] = ledger.embedder.execution()
            finally:
                self.active.release()

        threading.Thread(target=work, name="staged-json-comparison", daemon=True).start()
