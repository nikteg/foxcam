from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import Settings, get_settings
from ..db import init_db
from ..mediamtx import MediaMtx
from ..pipeline.runner import start_background
from . import api, api_cameras, api_notify, api_profiles, api_worker, api_workers

log = logging.getLogger(__name__)

# Built React app (``pnpm --dir web build``). Repo layout: <repo>/web/dist.
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[4] / "web" / "dist"


def _web_dist() -> Path:
    return Path(os.environ.get("MEDIA_STREAMER_WEB_DIST", DEFAULT_WEB_DIST))


def create_app(settings: Settings | None = None, background: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings)
        app.state.settings = settings
        app.state.mediamtx = MediaMtx(settings)
        try:
            await app.state.mediamtx.sync_paths()
        except Exception as e:  # noqa: BLE001 - UI should work even if MediaMTX is down
            log.warning("could not configure MediaMTX (%s); is it running at %s?", e,
                        settings.mediamtx.api_url)
        tasks = start_background(settings) if background else []
        yield
        for t in tasks:
            t.cancel()
        await app.state.mediamtx.aclose()

    app = FastAPI(title="Media Streamer", version=__version__, lifespan=lifespan)
    app.include_router(api_worker.router)
    app.include_router(api_cameras.router)
    app.include_router(api_workers.router)
    app.include_router(api_profiles.router)
    app.include_router(api_notify.router)
    app.include_router(api.router)
    _mount_spa(app, _web_dist())
    return app


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the React build; unknown non-API paths fall back to index.html (client routing).

    During development run the Vite dev server instead (``pnpm --dir web dev``), which
    proxies /api to this server.
    """
    index = dist / "index.html"
    if not index.is_file():
        log.warning("web UI not built (%s missing); run `pnpm --dir web build` or use the "
                    "Vite dev server", index)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
