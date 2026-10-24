"""The login this installation uses with MediaMTX.

MediaMTX does not trust local addresses: anything on the box could ask it for any camera,
including a camera source pointing back at MediaMTX itself
(``rtsp://127.0.0.1:8554/<other camera>/raw``), or add a path whose ``runOnInit`` runs a
command through its control API. The server (API, live view, snapshots), ingest and the
transcoders MediaMTX starts sign in as ``media-streamer`` with a password only they know:

- ``mediamtx.password`` in config.yaml (or ``$MEDIA_MTX_PASSWORD``), else a random one
  kept in ``<data_dir>/mediamtx/password``: a folder of its own, so Docker can share just
  it (and the recordings) with the MediaMTX container;
- ``media-streamer mediamtx`` starts MediaMTX with it (``MTX_AUTHINTERNALUSERS_0_PASS``,
  the first user in mediamtx.yml) and passes it on to the hooks it runs in
  ``$MEDIA_MTX_PASSWORD``, where ``media-streamer transcode`` picks it up.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .config import Settings

USER = "media-streamer"
ENV = "MEDIA_MTX_PASSWORD"
FILE = Path("mediamtx") / "password"
OLD_FILE = "mediamtx-password"  # before it had a folder of its own
# The first entry of authInternalUsers in mediamtx/mediamtx.yml.
MTX_ENV = "MTX_AUTHINTERNALUSERS_0_PASS"


def password(settings: Settings) -> str:
    if settings.mediamtx.password:
        return settings.mediamtx.password
    if env := os.environ.get(ENV):
        return env
    path = password_file(settings)
    old = settings.data_dir / OLD_FILE
    if old.is_file() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        old.replace(path)
    return stored(path)


def password_file(settings: Settings) -> Path:
    return settings.data_dir / FILE


def stored(path: Path) -> str:
    """Created once; every process (server, ingest, the MediaMTX launcher) reads it."""
    if path.is_file() and (pw := path.read_text().strip()):
        return pw
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(secrets.token_urlsafe(24))
    tmp.chmod(0o600)
    try:
        os.link(tmp, path)  # atomic and never overwrites: processes starting together agree
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text().strip()


def with_login(url: str, pw: str | None) -> str:
    """``url`` with the MediaMTX login, unless it already has one (or there is none)."""
    if not pw:
        return url
    parts = urlsplit(url)
    if "@" in parts.netloc:
        return url
    netloc = f"{USER}:{quote(pw, safe='')}@{parts.netloc}"
    return urlunsplit(parts._replace(netloc=netloc))


def url(settings: Settings, mediamtx_path: str) -> str:
    """RTSP URL of a MediaMTX path, signed in."""
    base = settings.mediamtx.rtsp_url.rstrip("/")
    return with_login(f"{base}/{mediamtx_path}", password(settings))


def api_auth(settings: Settings) -> tuple[str, str]:
    """httpx ``auth`` for MediaMTX's control API."""
    return (USER, password(settings))


def basic_auth(settings: Settings) -> str:
    """``Authorization`` header value for MediaMTX's HLS / WebRTC servers."""
    token = base64.b64encode(f"{USER}:{password(settings)}".encode()).decode()
    return f"Basic {token}"


def process_env(pw: str) -> dict[str, str]:
    """Environment for the MediaMTX process."""
    return {MTX_ENV: pw, ENV: pw}


def is_local(url: str) -> bool:
    """A MediaMTX address on this machine (what the hooks get, via ``$RTSP_PORT``)."""
    host = urlsplit(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")
