"""The processes' log files (Settings → Logs, admins only); see foxcam/logs.py."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import logs
from ..config import Settings
from .deps import require_admin, settings_dep

router = APIRouter(prefix="/api", tags=["logs"], dependencies=[Depends(require_admin)])

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


@router.get("/logs")
def log_files(settings: Settings = Depends(settings_dep)) -> list[dict[str, Any]]:
    """The processes with a log file, its size (all its rotated files) and last write."""
    return logs.available(settings.data_dir)


def _process(process: str) -> str:
    """server, ingest, compact, mediamtx or worker-<id>."""
    if not logs.known(process):
        raise HTTPException(404, "no such log")
    return process


@router.get("/logs/{process}")
def log_entries(process: str = Depends(_process), level: Level | None = None,
                q: str | None = Query(None, max_length=200, description="text to look for"),
                limit: int = Query(500, ge=1, le=5000),
                settings: Settings = Depends(settings_dep)) -> list[dict[str, str]]:
    """The newest entries first: {ts (UTC), level, logger, message}."""
    return logs.read(settings.data_dir, process, limit, level, q)


@router.get("/logs/{process}/download")
def log_download(process: str = Depends(_process),
                 settings: Settings = Depends(settings_dep)) -> StreamingResponse:
    if not logs.files(settings.data_dir, process):
        raise HTTPException(404, "no log for this process")
    return StreamingResponse(
        logs.chunks(settings.data_dir, process), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="foxcam-{process}.log"'})
