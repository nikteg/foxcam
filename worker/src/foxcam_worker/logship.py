"""Send this worker's log to the server (POST /api/worker/logs), so Settings → Logs shows
it next to the server's own, wherever the worker runs.

Records wait in a bounded buffer (the oldest go when the server is away for long) and a
background thread sends them every few seconds. Sending never blocks or breaks the worker:
failures keep the lines for the next try, and a server without the endpoint (404) stops it.
The server takes secrets out before writing them.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

import httpx

BUFFER = 5000
BATCH = 500


class LogShipper(logging.Handler):
    def __init__(self, http: httpx.Client, worker_id: str, every: float = 10.0):
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter())  # for tracebacks
        self.http = http
        self.worker_id = worker_id
        self.every = every
        self.buffer: deque[dict[str, Any]] = deque(maxlen=BUFFER)
        self.buffer_lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="log-shipper", daemon=True)

    def start(self) -> LogShipper:
        logging.getLogger().addHandler(self)
        self.thread.start()
        return self

    def emit(self, record: logging.LogRecord) -> None:
        # Not the sender's own trouble (httpx), or it would feed itself.
        if record.name.startswith(("httpx", "httpcore")):
            return
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + self.formatter.formatException(record.exc_info)  # type: ignore[union-attr]
            entry = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                         timespec="milliseconds").replace("+00:00", "Z"),
                     "level": record.levelname, "logger": record.name,
                     "message": message[:20000]}
        except Exception:  # noqa: BLE001 - a bad record is dropped, never raised
            return
        with self.buffer_lock:
            self.buffer.append(entry)

    def flush_to_server(self) -> bool:
        """Send what is waiting. False when the server has no such endpoint."""
        while True:
            with self.buffer_lock:
                batch = [self.buffer[i] for i in range(min(BATCH, len(self.buffer)))]
            if not batch:
                return True
            try:
                r = self.http.post("/api/worker/logs",
                                   json={"worker_id": self.worker_id, "entries": batch})
            except httpx.HTTPError:
                return True  # try again later
            if r.status_code == 404:
                return False
            if r.status_code >= 400:
                return True  # e.g. 429 (too many workers' logs): keep them, try later
            with self.buffer_lock:
                for _ in batch:
                    if self.buffer:
                        self.buffer.popleft()

    def _run(self) -> None:
        while not self.stopped.wait(self.every):
            if not self.flush_to_server():
                logging.getLogger().removeHandler(self)
                return

    def stop(self) -> None:
        """Send the last lines (e.g. on exit) and stop."""
        self.stopped.set()
        logging.getLogger().removeHandler(self)
        self.flush_to_server()
