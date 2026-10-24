"""HTTP client for the media-streamer worker protocol (docs/WORKER_PROTOCOL.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class LeaseLost(Exception):
    """The server no longer recognises our lease (expired or reassigned)."""


class Client:
    def __init__(self, server: str, worker_id: str, token: str | None = None,
                 info: dict[str, Any] | None = None, tags: list[str] | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=server.rstrip("/"), headers=headers,
                                 timeout=httpx.Timeout(30, read=300))
        self.worker_id = worker_id
        self.info = info or {}
        self.tags = tags or []

    def close(self) -> None:
        self.http.close()

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code == 409:
            raise LeaseLost(r.text)
        r.raise_for_status()
        return r

    def lease(self) -> dict[str, Any] | None:
        r = self._check(self.http.post("/api/worker/lease",
                                       json={"worker_id": self.worker_id, "info": self.info,
                                             "tags": self.tags}))
        return None if r.status_code == 204 else r.json()

    def download_input(self, job: dict[str, Any], dst: Path) -> None:
        with self.http.stream("GET", job["input_url"],
                              headers={"X-Lease-Token": job["lease_token"]}) as r:
            self._check(r)
            with dst.open("wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)

    def heartbeat(self, job: dict[str, Any]) -> None:
        self._check(self.http.post(f"/api/worker/jobs/{job['job_id']}/heartbeat",
                                   json={"lease_token": job["lease_token"]}))

    def result(self, job: dict[str, Any], detections: list[dict[str, Any]], model: str,
               processing_ms: int) -> None:
        self._check(self.http.post(f"/api/worker/jobs/{job['job_id']}/result", json={
            "lease_token": job["lease_token"], "model": model, "processing_ms": processing_ms,
            "detections": detections,
        }))

    def fail(self, job: dict[str, Any], error: str, retryable: bool = True) -> None:
        self._check(self.http.post(f"/api/worker/jobs/{job['job_id']}/fail", json={
            "lease_token": job["lease_token"], "error": error, "retryable": retryable,
        }))
