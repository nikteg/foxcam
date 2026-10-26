"""Motion detection on the derivative a segment's workers get.

The derivative (low fps, censor areas blacked out) is decoded once more as tiny greyscale
frames (``WIDTH`` x ``HEIGHT``: the downscale averages most sensor noise away, also the
grain of IR night pictures). Each frame is compared with a running average of the ones
before it. A pixel counts as changed when it differs by more than the sensitivity's
threshold from every background pixel around it (3x3), so edges that wobble by a pixel
(camera shake, compression) do not count. Changed pixels count in cells of 2x2 that need
at least two each. Connected cells make a region, and regions smaller than the
sensitivity's minimum are dropped (a leaf, a raindrop), and so are regions where nothing
differs from both the previous and the next frame: the place something just left (it
looks the same in the next frame), still in the background for a few frames. A region whose pattern is the same
as before, only brighter or darker, is light (a flickering lamp, headlights, a cloud's
shadow) and dropped too. When more than ``motion.light_change_fraction`` of the picture
changes at once, the frame is a light change (IR switching on, exposure), not motion, and
the average starts over from it. The camera's motion-ignore areas never count.

The per-pixel work is numpy (a 5-minute recording at 2 fps in a fraction of a second; pure
Python took several seconds, which on a small server starved preprocessing); only the few
changed cells are grouped into regions in Python.

The result is stored on the segment: ``motion_score`` (the largest share of the picture's
cells that moved in one frame, 0 = no motion), ``motion_boxes`` (per frame with motion,
the normalized boxes around its regions) and ``motion_faint`` (the same for regions that
missed the size or the previous-and-next-frame test: a small dog sniffing around, a person
walking away; they count only for a detection's box they lie in). Profiles use it to skip recordings without
motion (``skip_without_motion``), and to tell a target standing still from one that moves
(``still_minutes``, pipeline/still.py).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import ffmpeg

WIDTH, HEIGHT = 96, 54
CELL = 2  # pixels per cell side
COLS, ROWS = WIDTH // CELL, HEIGHT // CELL

Box = list[float]  # normalized [x, y, w, h]


@dataclass(frozen=True)
class Level:
    threshold: int  # luma change (0..255) for a pixel to count
    min_cells: int  # cells a region needs


LEVELS: dict[str, Level] = {
    "low": Level(threshold=32, min_cells=6),
    "medium": Level(threshold=22, min_cells=3),
    "high": Level(threshold=14, min_cells=1),
}
# IR night pictures are noisier: thresholds go up by this much.
NIGHT_FACTOR = 1.4
# How fast the background follows the picture (share of the new frame per step).
ALPHA = 0.5
# A region whose pixels correlate this well with the background's is light, not motion.
LIGHT_CORRELATION = 0.85
# Both flat (standard deviation below this): a uniform change is light too.
FLAT_STD = 5.0


@dataclass
class Motion:
    score: float  # 0..1
    boxes: list[list[Any]]  # [[offset_ms, [box, ...]], ...], frames with motion only
    # The same for faint motion: regions too small for the sensitivity, or that changed
    # only since the last frame or only before the next one (something slow, small or
    # walking straight towards the camera). Not motion on its own; it counts for a
    # detection whose box it lies in (pipeline/still.py).
    faint: list[list[Any]] = field(default_factory=list)


def decode(video: Path, fps: float, start_s: float = 0.0,
           duration_s: float | None = None) -> list[bytes]:
    """The video's frames at ``fps`` as ``WIDTH`` x ``HEIGHT`` greyscale bytes (from
    ``start_s``, for ``duration_s``: a segment in its hourly file)."""
    part = ["-t", f"{duration_s:.3f}"] if duration_s else []
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}", "-i", str(video), *part, "-an",
         "-vf", f"{ffmpeg.sample(fps)},scale={WIDTH}:{HEIGHT}:flags=area,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"motion: ffmpeg failed: {proc.stderr.decode()[-300:]}")
    size = WIDTH * HEIGHT
    data = proc.stdout
    return [data[i:i + size] for i in range(0, len(data) - size + 1, size)]


def ignore_mask(rects: list[Box] | None) -> np.ndarray:
    """Per pixel (``HEIGHT`` x ``WIDTH``): inside one of the motion-ignore areas."""
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for x, y, w, h in rects or []:
        x0, y0 = int(x * WIDTH), int(y * HEIGHT)
        x1, y1 = min(WIDTH, int((x + w) * WIDTH + 0.999)), min(HEIGHT, int((y + h) * HEIGHT + 0.999))
        mask[y0:y1, x0:x1] = True
    return mask


def analyse(frames: list[bytes], fps: float, sensitivity: str = "medium", night: bool = False,
            ignore: list[Box] | None = None, light_change_fraction: float = 0.5) -> Motion:
    level = LEVELS.get(sensitivity, LEVELS["medium"])
    threshold = level.threshold * (NIGHT_FACTOR if night else 1.0)
    skip = ignore_mask(ignore)
    counted = int((~skip).sum()) or 1
    images = [np.frombuffer(f, dtype=np.uint8).reshape(HEIGHT, WIDTH).astype(np.float64)
              for f in frames]
    score = 0.0
    out: list[list[Any]] = []
    faint: list[list[Any]] = []
    background: np.ndarray | None = None
    # Each frame's 3x3 spread serves twice: compared with the frame after it and before it.
    previous = following = _spread(images[0]) if images else None
    for i, frame in enumerate(images):
        here = following
        following = _spread(images[i + 1]) if i + 1 < len(images) else None
        if background is None:
            background, previous = frame, here
            continue
        changed = _changed(frame, _spread(background), threshold, skip)
        before = _changed(frame, previous, threshold, skip)
        after = _changed(frame, following, threshold, skip) if following is not None else before
        previous = here
        if changed.sum() > light_change_fraction * counted:
            background = frame  # light change: start over
            continue
        moved_both = _cell_any(before & after)  # changed since the last frame and before the next
        moved_either = _cell_any(before | after)
        regions: list[list[tuple[int, int]]] = []
        weak: list[list[tuple[int, int]]] = []
        for r in _regions(_cells(changed)):
            if not any(moved_either[cy, cx] for cx, cy in r) or _lit(r, frame, background):
                continue
            if len(r) >= level.min_cells and any(moved_both[cy, cx] for cx, cy in r):
                regions.append(r)
            else:
                weak.append(r)
        background = background + ALPHA * (frame - background)
        at = round(i * 1000 / fps)
        if weak:
            faint.append([at, [_box(r) for r in weak]])
        if not regions:
            continue
        moving = sum(len(r) for r in regions)
        score = max(score, moving / (COLS * ROWS))
        out.append([at, [_box(r) for r in regions]])
    return Motion(round(score, 4), out, faint)


def _changed(frame: np.ndarray, spread: tuple[np.ndarray, np.ndarray], threshold: float,
             skip: np.ndarray) -> np.ndarray:
    """Per pixel: differs by more than ``threshold`` from every reference pixel around it
    (``spread``: the reference's 3x3 lowest and highest values)."""
    low, high = spread
    return ~skip & ((frame > high + threshold) | (frame < low - threshold))


def _spread(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel the lowest and highest value around it (3x3)."""
    def pick(img: np.ndarray, fn, edge: float) -> np.ndarray:
        p = np.pad(img, 1, constant_values=edge)
        rows = fn(fn(p[:, :-2], p[:, 1:-1]), p[:, 2:])
        return fn(fn(rows[:-2], rows[1:-1]), rows[2:])

    return pick(image, np.minimum, np.inf), pick(image, np.maximum, -np.inf)


def _lit(region: list[tuple[int, int]], frame: np.ndarray, background: np.ndarray) -> bool:
    """The region (with a margin) has the background's pattern, only brighter or darker:
    light, not something moving."""
    xs, ys = [c[0] for c in region], [c[1] for c in region]
    x0, x1 = max(0, min(xs) * CELL - 2), min(WIDTH, (max(xs) + 1) * CELL + 2)
    y0, y1 = max(0, min(ys) * CELL - 2), min(HEIGHT, (max(ys) + 1) * CELL + 2)
    a, b = frame[y0:y1, x0:x1].ravel(), background[y0:y1, x0:x1].ravel()
    n = a.size
    da, db = a - a.mean(), b - b.mean()
    va, vb = float(da @ da), float(db @ db)
    if va < n * FLAT_STD ** 2 and vb < n * FLAT_STD ** 2:
        return True
    if not va or not vb:
        return False
    return float(da @ db) / (va * vb) ** 0.5 >= LIGHT_CORRELATION


def _cell_any(pixels: np.ndarray) -> np.ndarray:
    """Per cell (``ROWS`` x ``COLS``): any of its pixels set."""
    return pixels.reshape(ROWS, CELL, COLS, CELL).any(axis=(1, 3))


def _cells(changed: np.ndarray) -> set[tuple[int, int]]:
    """Cells with at least two changed pixels of their four."""
    counts = changed.reshape(ROWS, CELL, COLS, CELL).sum(axis=(1, 3))
    return {(int(cx), int(cy)) for cy, cx in zip(*np.nonzero(counts >= 2), strict=True)}


def _regions(cells: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Groups of touching cells (diagonals included)."""
    left, regions = set(cells), []
    while left:
        todo, region = [left.pop()], []
        while todo:
            cx, cy = todo.pop()
            region.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n in left:
                        left.remove(n)
                        todo.append(n)
        regions.append(region)
    return regions


def _box(region: list[tuple[int, int]]) -> Box:
    xs, ys = [c[0] for c in region], [c[1] for c in region]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    return [round(x0 / COLS, 4), round(y0 / ROWS, 4),
            round((x1 - x0) / COLS, 4), round((y1 - y0) / ROWS, 4)]


def measure(video: Path, fps: float, **kwargs: Any) -> Motion:
    return analyse(decode(video, fps), fps, **kwargs)

