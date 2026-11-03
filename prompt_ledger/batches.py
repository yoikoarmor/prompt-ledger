"""Single bounded dry-run transaction for sequential browser uploads."""

import queue
import threading
import time
from concurrent.futures import Future

from .config import LedgerError


class DryBatch:
    def __init__(self, ledger):
        self.ledger = ledger
        self.queue = queue.Queue(maxsize=1)
        self.state_lock = threading.Lock()
        self.closed = threading.Event()
        self.finished = threading.Event()
        self.last_activity = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            with self.ledger.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    while not self.closed.is_set():
                        try:
                            item = self.queue.get(timeout=1)
                        except queue.Empty:
                            if time.monotonic() - self.last_activity > 120:
                                break
                            continue
                        if item is None:
                            break
                        name, raw, future = item
                        try:
                            future.set_result(self.ledger._add(conn, name, raw))
                        except Exception as exc:  # noqa: BLE001 - propagate to API thread
                            future.set_exception(exc)
                            break
                finally:
                    conn.rollback()
        finally:
            with self.state_lock:
                self.closed.set()
                while not self.queue.empty():
                    item = self.queue.get_nowait()
                    if item is not None:
                        item[2].set_exception(
                            LedgerError("batch_closed", "Dry-run expired; restart the selection.")
                        )
                self.finished.set()

    def submit(self, name, raw):
        with self.state_lock:
            if self.closed.is_set():
                raise LedgerError("batch_closed", "Dry-run expired; restart the selection.")
            future = Future()
            self.last_activity = time.monotonic()
            try:
                self.queue.put_nowait((name, raw, future))
            except queue.Full:
                raise LedgerError("batch_busy", "Send dry-run files sequentially.") from None
        return future.result(timeout=300)

    def close(self):
        with self.state_lock:
            self.closed.set()
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                pass
        self.thread.join(timeout=5)
        return self.finished.is_set()
