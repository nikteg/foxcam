"""Timeline preview sprites for the web player (hover previews and the scrub picture).

Every video the player shows has one: a JPEG with a grid of small frames, plus a WebVTT
file mapping each time range to a tile (``#xywh=``). One rule for all of them: a video's
sprite is made when the video is made, lives in ``sprites/<kind>-<id>`` as long as the
video does, and goes when it goes; made again on request when missing or older than the
video (a re-cut clip, a recording now read from its archive). The kinds differ only in
``KINDS``:

- ``segment``: a recording, a tile every 2 s from keyframes only (cheap: preparation makes
  it for every recording);
- ``archive``: an hour of a camera, a tile every 10 s from keyframes;
- ``clip``: an event's clip, short and scrubbed closely, a tile every half second from
  every frame, twice as wide (it fills the player while scrubbing).
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select

from . import ffmpeg
from .config import Settings
from .db import session_scope
from .models import Archive, Event

log = logging.getLogger(__name__)

COLUMNS = 10

SpriteKind = Literal["segment", "archive", "clip"]


@dataclass(frozen=True)
class Kind:
    interval: float  # seconds per tile
    keyframes_only: bool  # cheap, but a tile can show a frame up to a keyframe early
    tile_width: int


KINDS: dict[SpriteKind, Kind] = {
    "segment": Kind(interval=2.0, keyframes_only=True, tile_width=160),
    "archive": Kind(interval=10.0, keyframes_only=True, tile_width=160),
    "clip": Kind(interval=0.5, keyframes_only=False, tile_width=320),
}


@dataclass
class Sprite:
    interval: float
    duration: float
    tiles: int
    columns: int
    tile_w: int
    tile_h: int


def files(settings: Settings, kind: SpriteKind, item_id: int) -> tuple[Path, Path]:
    folder = settings.sprites_dir
    return folder / f"{kind}-{item_id}.jpg", folder / f"{kind}-{item_id}.json"


@dataclass
class _Source:
    path: Path
    offset_s: float
    duration_s: float


def _source(settings: Settings, kind: SpriteKind, item_id: int) -> _Source | None:
    """The video a sprite is made from; None when it is gone."""
    if kind == "segment":
        from .pipeline.compaction import source_for  # deferred: compaction imports this

        src = source_for(settings, item_id)  # its own file, or its part of an archive
        return src and _Source(src.path, src.offset_s if src.archived else 0.0,
                               src.duration_s)
    with session_scope() as s:
        if kind == "archive":
            arc = s.get(Archive, item_id)
            if arc is None or arc.deleted_at is not None:
                return None
            return _Source(settings.recordings_dir / arc.path, 0.0, arc.duration_s)
        ev = s.get(Event, item_id)
        if ev is None or not ev.clip_path:
            return None
        return _Source(settings.data_dir / ev.clip_path, 0.0, ev.clip_duration_s or 0.0)


def get(settings: Settings, kind: SpriteKind, item_id: int) -> Sprite | None:
    """The video's sprite, made when missing or older than the video. None when there is
    neither (the video is gone and never had one)."""
    image, meta = files(settings, kind, item_id)
    src = _source(settings, kind, item_id)
    on_disk = src is not None and src.path.exists()
    if image.exists() and (not on_disk or image.stat().st_mtime >= src.path.stat().st_mtime) \
            and (sprite := load(meta)):
        return sprite
    if not on_disk or src.duration_s <= 0:
        return None
    k = KINDS[kind]
    return build(src.path, image, meta, duration=src.duration_s, interval=k.interval,
                 offset=src.offset_s, keyframes_only=k.keyframes_only,
                 tile_width=k.tile_width)


def remove(settings: Settings, kind: SpriteKind, item_id: int) -> None:
    """With the video."""
    for path in files(settings, kind, item_id):
        path.unlink(missing_ok=True)


def remove_kind(settings: Settings, kind: SpriteKind) -> None:
    """All of a kind (e.g. every clip, when the events are made again)."""
    if settings.sprites_dir.is_dir():
        for path in settings.sprites_dir.glob(f"{kind}-*"):
            path.unlink(missing_ok=True)


def remove_all(settings: Settings) -> None:
    shutil.rmtree(settings.sprites_dir, ignore_errors=True)


def load(meta_path: Path) -> Sprite | None:
    try:
        return Sprite(**json.loads(meta_path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def build(src: Path, image: Path, meta: Path, *, duration: float, interval: float,
          offset: float = 0.0, keyframes_only: bool = True,
          tile_width: int = 160) -> Sprite:
    """Make the sprite of ``src`` from ``offset`` for ``duration`` seconds. With
    ``keyframes_only`` a tile shows the last keyframe before its time (cheap, but up to a
    keyframe interval early); otherwise the frame on screen then."""
    tiles = max(1, math.ceil(duration / interval))
    rows = math.ceil(tiles / COLUMNS)
    columns = min(COLUMNS, tiles)
    image.parent.mkdir(parents=True, exist_ok=True)
    part = image.with_suffix(".part.jpg")
    args = ["ffmpeg", "-v", "error", "-y", "-hwaccel", "auto"]
    if keyframes_only:
        args += ["-skip_frame", "nokey"]
    if offset:
        args += ["-ss", f"{offset:.3f}"]
    args += ["-t", f"{duration:.3f}", "-i", str(src),
             "-vf", f"{ffmpeg.sample(f'1/{interval}')},scale={tile_width}:-2,tile={columns}x{rows}",
             "-frames:v", "1", "-q:v", "5", str(part)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        tail = proc.stderr.strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"sprite: {tail[0]}")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(part)], capture_output=True, text=True,
                         check=True).stdout.strip()
    width, height = (int(v) for v in out.split(",")[:2])
    sprite = Sprite(interval=interval, duration=duration, tiles=tiles, columns=columns,
                    tile_w=width // columns, tile_h=height // rows)
    part.replace(image)
    meta.write_text(json.dumps(asdict(sprite)))
    return sprite


def _ts(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def vtt(sprite: Sprite, image_url: str) -> str:
    """WebVTT thumbnails track: each tile's time range and place in the image."""
    lines = ["WEBVTT", ""]
    for i in range(sprite.tiles):
        start = i * sprite.interval
        end = min((i + 1) * sprite.interval, sprite.duration)
        if end <= start:
            break
        x, y = i % sprite.columns * sprite.tile_w, i // sprite.columns * sprite.tile_h
        lines += [f"{_ts(start)} --> {_ts(end)}",
                  f"{image_url}#xywh={x},{y},{sprite.tile_w},{sprite.tile_h}", ""]
    return "\n".join(lines)


def backfill_archives(settings: Settings, limit: int = 1) -> int:
    """Sprites for archives made before there were any (newest first)."""
    made = 0
    with session_scope() as s:
        ids = list(s.scalars(select(Archive.id).where(Archive.deleted_at.is_(None))
                             .order_by(Archive.start_at.desc())))
    for archive_id in ids:
        if made >= limit:
            break
        image, meta = files(settings, "archive", archive_id)
        if image.exists():
            continue
        try:
            if get(settings, "archive", archive_id):
                made += 1
        except Exception as e:  # noqa: BLE001 - a broken file must not stop the others
            log.warning("archive %s: %s", archive_id, e)
            meta.write_text("{}")  # not again every round
            image.touch()
    return made
