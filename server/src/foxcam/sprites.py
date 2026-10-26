"""Thumbnail sprites for scrubbing a recording's timeline (the web player's hover preview).

One JPEG per segment or archive with a grid of small frames (``TILE_WIDTH`` px wide),
plus a WebVTT file mapping each time range to a tile (``#xywh=``), which the player reads.
Only keyframes are decoded (``-skip_frame nokey``) and a hardware decoder is used when
there is one, so this is cheap: a 60 s segment takes well under a second; an hour of 3K
takes ~20 s, so archives get theirs from the archiving process right after they are built
(older ones, up to ten per pass, when it has nothing to join) and segments theirs when
they are preprocessed; either is made on first play if still missing.

Archive sprites live in ``thumbs/sprites`` and go with their archive; segment sprites are a
cache (``cache/sprites``) cleaned up by age.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select

from . import ffmpeg
from .config import Settings
from .db import session_scope
from .models import Archive

log = logging.getLogger(__name__)

TILE_WIDTH = 160
COLUMNS = 10
SEGMENT_INTERVAL = 2.0  # seconds per tile (cameras send a keyframe every 1-2 s)
ARCHIVE_INTERVAL = 10.0


@dataclass
class Sprite:
    interval: float
    duration: float
    tiles: int
    columns: int
    tile_w: int
    tile_h: int


def segment_files(settings: Settings, segment_id: int) -> tuple[Path, Path]:
    folder = settings.data_dir / "cache" / "sprites"
    return folder / f"segment-{segment_id}.jpg", folder / f"segment-{segment_id}.json"


def archive_files(settings: Settings, archive_id: int) -> tuple[Path, Path]:
    folder = settings.thumbs_dir / "sprites"
    return folder / f"archive-{archive_id}.jpg", folder / f"archive-{archive_id}.json"


def load(meta_path: Path) -> Sprite | None:
    try:
        return Sprite(**json.loads(meta_path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def build(src: Path, image: Path, meta: Path, *, duration: float, interval: float,
          offset: float = 0.0) -> Sprite:
    """Make the sprite of ``src`` from ``offset`` for ``duration`` seconds."""
    tiles = max(1, math.ceil(duration / interval))
    rows = math.ceil(tiles / COLUMNS)
    columns = min(COLUMNS, tiles)
    image.parent.mkdir(parents=True, exist_ok=True)
    part = image.with_suffix(".part.jpg")
    args = ["ffmpeg", "-v", "error", "-y", "-hwaccel", "auto", "-skip_frame", "nokey"]
    if offset:
        args += ["-ss", f"{offset:.3f}"]
    args += ["-t", f"{duration:.3f}", "-i", str(src),
             "-vf", f"{ffmpeg.sample(f'1/{interval}')},scale={TILE_WIDTH}:-2,tile={columns}x{rows}",
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


def cues(sprite: Sprite, image_url: str, shift: float = 0.0,
         until: float = math.inf) -> list[str]:
    """Each tile's time range and place in the image, as WebVTT cue lines; with ``shift``
    seconds added to the times and only what lies in [0, ``until``) (a clip's part)."""
    lines = []
    for i in range(sprite.tiles):
        start = i * sprite.interval
        end = min((i + 1) * sprite.interval, sprite.duration)
        if end <= start:
            break
        start, end = max(start + shift, 0.0), min(end + shift, until)
        if end <= start:
            continue
        x, y = i % sprite.columns * sprite.tile_w, i // sprite.columns * sprite.tile_h
        lines += [f"{_ts(start)} --> {_ts(end)}",
                  f"{image_url}#xywh={x},{y},{sprite.tile_w},{sprite.tile_h}", ""]
    return lines


def vtt(sprite: Sprite, image_url: str) -> str:
    """WebVTT thumbnails track: each tile's time range and place in the image."""
    return "\n".join(["WEBVTT", "", *cues(sprite, image_url)])


def archive_sprite(settings: Settings, archive_id: int) -> Sprite | None:
    """Make (or reuse) an archive's sprite. None when the file is gone."""
    image, meta = archive_files(settings, archive_id)
    if image.exists() and (sprite := load(meta)):
        return sprite
    with session_scope() as s:
        arc = s.get(Archive, archive_id)
        if arc is None or arc.deleted_at is not None:
            return None
        src, duration = settings.recordings_dir / arc.path, arc.duration_s
    if not src.exists() or duration <= 0:
        return None
    return build(src, image, meta, duration=duration, interval=ARCHIVE_INTERVAL)


def segment_sprite(settings: Settings, segment_id: int) -> Sprite | None:
    """Make (or reuse) a segment's sprite, from its own file or its part of an archive.
    None when the footage is gone."""
    from .pipeline.compaction import source_for  # deferred: compaction imports this module

    image, meta = segment_files(settings, segment_id)
    if image.exists() and (sprite := load(meta)):
        return sprite
    src = source_for(settings, segment_id)
    if src is None or not src.path.exists() or src.duration_s <= 0:
        return None
    return build(src.path, image, meta, duration=src.duration_s, interval=SEGMENT_INTERVAL,
                 offset=src.offset_s if src.archived else 0.0)


def backfill_archives(settings: Settings, limit: int = 1) -> int:
    """Sprites for archives made before there were any (newest first)."""
    made = 0
    with session_scope() as s:
        ids = list(s.scalars(select(Archive.id).where(Archive.deleted_at.is_(None))
                             .order_by(Archive.start_at.desc())))
    for archive_id in ids:
        if made >= limit:
            break
        if archive_files(settings, archive_id)[0].exists():
            continue
        try:
            if archive_sprite(settings, archive_id):
                made += 1
        except Exception as e:  # noqa: BLE001 - a broken file must not stop the others
            log.warning("archive %s: %s", archive_id, e)
            archive_files(settings, archive_id)[1].write_text("{}")  # don't retry forever
            archive_files(settings, archive_id)[0].touch()
    return made


def remove_archive(settings: Settings, archive_id: int) -> None:
    for path in archive_files(settings, archive_id):
        path.unlink(missing_ok=True)
