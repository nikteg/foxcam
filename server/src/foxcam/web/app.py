from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, runtime_settings
from ..accounts import announce_setup
from ..config import Settings, get_settings
from ..db import init_db
from ..mediamtx import MediaMtx
from ..pipeline.runner import start_background
from . import (
    api,
    api_auth,
    api_cameras,
    api_notify,
    api_profiles,
    api_push,
    api_users,
    api_worker,
    api_workers,
    live,
)
from .deps import authenticate

log = logging.getLogger(__name__)

# Built React app (``pnpm --dir web build``). Repo layout: <repo>/web/dist.
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[4] / "web" / "dist"


def _web_dist() -> Path:
    return Path(os.environ.get("FOXCAM_WEB_DIST", DEFAULT_WEB_DIST))


async def _settings_loop(settings: Settings, mtx: MediaMtx) -> None:
    """Tunables changed in the web UI or with `foxcam settings` (the API applies its
    own changes right away). Segment length and the low-data live view go to MediaMTX."""
    while True:
        await asyncio.sleep(runtime_settings.REFRESH_SECONDS)
        try:
            changed = await asyncio.to_thread(runtime_settings.refresh, settings)
            if changed & runtime_settings.MEDIAMTX_KEYS:
                await mtx.sync_paths()
        except Exception:
            log.exception("applying settings failed")


def create_app(settings: Settings | None = None, background: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings)
        app.state.settings = settings
        if not settings.auth.enabled:
            log.warning("auth.enabled is false: everyone who can reach this server is an admin")
        announce_setup(settings)
        app.state.mediamtx = MediaMtx(settings)
        try:
            await app.state.mediamtx.sync_paths()
        except Exception as e:  # noqa: BLE001 - UI should work even if MediaMTX is down
            log.warning("could not configure MediaMTX (%s); is it running at %s?", e,
                        settings.mediamtx.api_url)
        tasks = start_background(settings) if background else []
        tasks.append(asyncio.create_task(_settings_loop(settings, app.state.mediamtx)))
        yield
        for t in tasks:
            t.cancel()
        await app.state.mediamtx.aclose()
        if client := getattr(app.state, "live_client", None):
            await client.aclose()

    # `authenticate` runs for every route: the API is private unless a route opts out.
    # The API docs (/docs, /redoc, /openapi.json) are outside the login: development only.
    docs = os.environ.get("FOXCAM_API_DOCS") == "1"
    app = FastAPI(title="Foxcam", version=__version__, lifespan=lifespan,
                  dependencies=[Depends(authenticate)],
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Invite links carry their token in the URL: never leak it to other sites.
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # No framing by other sites (clickjacking); the UI frames its own live players.
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    app.include_router(api_auth.router)
    app.include_router(api_users.router)
    app.include_router(live.router)
    app.include_router(api_worker.router)
    app.include_router(api_cameras.router)
    app.include_router(api_workers.router)
    app.include_router(api_profiles.router)
    app.include_router(api_notify.router)
    app.include_router(api_push.router)
    app.include_router(api.router)
    _mount_spa(app, _web_dist())
    return app


_BUILD_META = re.compile(r'<meta name="foxcam-build" content="([^"]+)"')


def _build_id(index: Path) -> str | None:
    """The web build's id (vite.config.ts writes it into index.html)."""
    found = _BUILD_META.search(index.read_text(errors="replace"))
    return found.group(1) if found else None


class BuildHeader:
    """Says on every API response which web build the server serves (X-Foxcam-Build), so
    a page left open across a deploy can tell and offer to reload (web/src/newVersion.ts).
    Plain ASGI, so streamed responses (video, live view) pass through untouched."""

    def __init__(self, app, build: str) -> None:
        self.app = app
        self.header = (b"x-foxcam-build", build.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        async def send_with_build(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), self.header]
            await send(message)

        return await self.app(scope, receive, send_with_build)


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
    build = _build_id(index)
    if build:
        app.add_middleware(BuildHeader, build=build)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
