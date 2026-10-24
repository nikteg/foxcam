"""Live view through the server: MediaMTX's WebRTC and HLS players behind the login.

Browsers never talk to MediaMTX's HTTP servers; they load
``/api/live/<camera>/<main|low>/<webrtc|hls>/`` here, which checks the session and the
user's access to the camera and forwards the request. MediaMTX itself only lets local
processes in (see mediamtx/mediamtx.yml). WebRTC media then flows directly between the
browser and MediaMTX's ICE port, for the sessions set up through this proxy.

Only the camera's own path and its low-data copy are reachable, never ``<camera>/raw``
(the picture before burned-in censor areas) or another camera: what follows the
protocol is one file name (``index.m3u8``, a segment) or a WHEP session, nothing with
``..`` or more levels. The proxy signs in to MediaMTX (mediamtx_auth.py).
"""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .. import mediamtx_auth
from ..accounts import Principal
from ..config import Settings
from ..mediamtx import low_path
from .deps import camera_for, principal, settings_dep

router = APIRouter(prefix="/api/live", tags=["live"])

# Request headers worth passing on (never cookies or our own auth).
FORWARD_REQUEST = ("content-type", "accept", "if-match", "range", "if-none-match")
MAX_BODY = 256 * 1024
FORWARD_RESPONSE = ("content-type", "cache-control", "etag", "accept-patch", "link",
                    "content-range", "accept-ranges", "access-control-expose-headers")


# What may follow the protocol, per method: the player page, a file of it (playlists,
# segments, scripts), the WHEP endpoint and a WHEP session. Never WHIP (publishing, which
# the proxy's MediaMTX login could do) or another path.
FILE = r"(?:[\w-]+(?:\.[\w-]+)+)?"  # index.m3u8, hls.min.js; never ..
REST = {"GET": re.compile(FILE), "POST": re.compile("whep"),
        "PATCH": re.compile(r"whep/[\w-]+"), "DELETE": re.compile(r"whep/[\w-]+")}


def live_url(camera_id: str, variant: str, proto: str) -> str:
    return f"/api/live/{camera_id}/{variant}/{proto}/"


def _client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "live_client", None)
    if client is None:
        # Low-latency HLS holds playlist requests open until the next part is ready.
        client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5))
        request.app.state.live_client = client
    return client


async def _body(request: Request) -> bytes:
    """WHEP offers and ICE candidates are a few kB."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise HTTPException(413, "request too large")
    return body


@router.api_route("/{camera_id}/{variant}/{proto}/{rest:path}",
                  methods=["GET", "POST", "PATCH", "DELETE"], include_in_schema=False)
async def live(camera_id: str, variant: str, proto: str, rest: str, request: Request,
               p: Principal = Depends(principal),
               settings: Settings = Depends(settings_dep)) -> StreamingResponse:
    if variant not in ("main", "low") or proto not in ("webrtc", "hls") \
            or not REST[request.method].fullmatch(rest):
        raise HTTPException(404, "not found")
    camera_for(p, settings, camera_id)
    m = settings.mediamtx
    if variant == "low" and not m.low_data.enabled:
        raise HTTPException(404, "low-data live view is turned off")
    path = camera_id if variant == "main" else low_path(camera_id)
    base = (m.webrtc_url if proto == "webrtc" else m.hls_url).rstrip("/")
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST}
    headers["authorization"] = mediamtx_auth.basic_auth(settings)
    client = _client(request)
    upstream = client.build_request(request.method, f"{base}/{path}/{rest}",
                                    params=request.query_params, headers=headers,
                                    content=await _body(request))
    # Second guard: whatever URL normalisation did, still exactly this camera's path.
    if upstream.url.path != f"{httpx.URL(base).path.rstrip('/')}/{path}/{rest}":
        raise HTTPException(404, "not found")
    try:
        resp = await client.send(upstream, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"live view unavailable ({type(e).__name__})") from e
    out = {k: v for k, v in resp.headers.items() if k.lower() in FORWARD_RESPONSE}
    if location := resp.headers.get("location"):
        # WHEP session URLs (/<path>/whep/<id>) must come back through this proxy too.
        prefix = f"/{path}/"
        if location.startswith(prefix):
            location = live_url(camera_id, variant, proto) + location[len(prefix):]
        out["location"] = location
    return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out,
                             background=BackgroundTask(resp.aclose))
