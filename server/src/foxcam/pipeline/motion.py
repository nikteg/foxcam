"""Motion detection on the derivative a segment's workers get.

The derivative (low fps, censor areas blacked out) is decoded once more as tiny greyscale
frames (``WIDTH`` x ``HEIGHT``: the downscale averages most sensor noise away, also the
grain of IR night pictures). Each frame is compared with a running average of the ones
before it. A pixel counts as changed when it differs by more than the sensitivity's
threshold from every background pixel around it (3x3), so edges that wobble by a pixel
(camera shake, compression) do not count. Changed pixels count in cells of 2x2 that need
at least two each. Connected cells make a region, and regions smaller than the
sensitivity's minimum are dropped (a leaf, a raindrop). A region whose pattern is the same
as before, only brighter or darker, is light (a flickering lamp, headlights, a cloud's
shadow) and dropped too. When more than ``motion.light_change_fraction`` of the picture
changes at once, the frame is a light change (IR switching on, exposure), not motion, and
the average starts over from it. The camera's motion-ignore areas never count.

Pure Python on purpose: at this size it takes well under a second per segment, and the
server needs no numpy or OpenCV.

The result is stored on the segment: ``motion_score`` (the largest share of the picture's
cells that moved in one frame, 0 = no motion) and ``motion_boxes`` (per frame with motion,
the normalized boxes around its regions). Profiles use it to skip recordings without
motion (``skip_without_motion``) and to require that a target's box overlaps motion
(``require_motion``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def decode(video: Path, fps: float) -> list[bytes]:
    """The video's frames at ``fps`` as ``WIDTH`` x ``HEIGHT`` greyscale bytes."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-an",
         "-vf", f"fps={fps},scale={WIDTH}:{HEIGHT}:flags=area,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=300, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"motion: ffmpeg failed: {proc.stderr.decode()[-300:]}")
    size = WIDTH * HEIGHT
    data = proc.stdout
    return [data[i:i + size] for i in range(0, len(data) - size + 1, size)]


def ignore_mask(rects: list[Box] | None) -> list[bool]:
    """Per pixel: inside one of the motion-ignore areas."""
    mask = [False] * (WIDTH * HEIGHT)
    for x, y, w, h in rects or []:
        x0, y0 = int(x * WIDTH), int(y * HEIGHT)
        x1, y1 = min(WIDTH, int((x + w) * WIDTH + 0.999)), min(HEIGHT, int((y + h) * HEIGHT + 0.999))
        for yy in range(y0, y1):
            row = yy * WIDTH
            for xx in range(x0, x1):
                mask[row + xx] = True
    return mask


def analyse(frames: list[bytes], fps: float, sensitivity: str = "medium", night: bool = False,
            ignore: list[Box] | None = None, light_change_fraction: float = 0.5) -> Motion:
    level = LEVELS.get(sensitivity, LEVELS["medium"])
    threshold = level.threshold * (NIGHT_FACTOR if night else 1.0)
    skip = ignore_mask(ignore)
    counted = sum(1 for v in skip if not v) or 1
    score = 0.0
    out: list[list[Any]] = []
    background: list[float] | None = None
    for i, frame in enumerate(frames):
        if background is None:
            background = [float(v) for v in frame]
            continue
        low, high = _spread(background)
        changed = [not skip[p] and (frame[p] > high[p] + threshold or frame[p] < low[p] - threshold)
                   for p in range(WIDTH * HEIGHT)]
        if sum(changed) > light_change_fraction * counted:
            background = [float(v) for v in frame]  # light change: start over
            continue
        cells = _cells(changed)
        regions = [r for r in _regions(cells)
                   if len(r) >= level.min_cells and not _lit(r, frame, background)]
        background = [b + ALPHA * (v - b) for b, v in zip(background, frame, strict=True)]
        if not regions:
            continue
        moving = sum(len(r) for r in regions)
        score = max(score, moving / (COLS * ROWS))
        out.append([round(i * 1000 / fps), [_box(r) for r in regions]])
    return Motion(round(score, 4), out)


def _spread(image: list[float]) -> tuple[list[float], list[float]]:
    """Per pixel the lowest and highest value around it (3x3)."""
    def rows(img: list[float], pick) -> list[float]:
        out = []
        for y in range(HEIGHT):
            r = img[y * WIDTH:(y + 1) * WIDTH]
            out += [pick(r[max(0, x - 1):x + 2]) for x in range(WIDTH)]
        return out

    def cols(img: list[float], pick) -> list[float]:
        return [pick(img[max(0, y - 1) * WIDTH + x:min(HEIGHT, y + 2) * WIDTH:WIDTH])
                for y in range(HEIGHT) for x in range(WIDTH)]

    return cols(rows(image, min), min), cols(rows(image, max), max)


def _lit(region: list[tuple[int, int]], frame: bytes, background: list[float]) -> bool:
    """The region (with a margin) has the background's pattern, only brighter or darker:
    light, not something moving."""
    xs, ys = [c[0] for c in region], [c[1] for c in region]
    x0, x1 = max(0, min(xs) * CELL - 2), min(WIDTH, (max(xs) + 1) * CELL + 2)
    y0, y1 = max(0, min(ys) * CELL - 2), min(HEIGHT, (max(ys) + 1) * CELL + 2)
    a = [frame[y * WIDTH + x] for y in range(y0, y1) for x in range(x0, x1)]
    b = [background[y * WIDTH + x] for y in range(y0, y1) for x in range(x0, x1)]
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((v - ma) ** 2 for v in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va < n * FLAT_STD ** 2 and vb < n * FLAT_STD ** 2:
        return True
    if not va or not vb:
        return False
    return sum((p - ma) * (q - mb) for p, q in zip(a, b, strict=True)) / (va * vb) ** 0.5 \
        >= LIGHT_CORRELATION


def _cells(changed: list[bool]) -> set[tuple[int, int]]:
    """Cells with at least two changed pixels of their four."""
    cells = set()
    for cy in range(ROWS):
        for cx in range(COLS):
            p = cy * CELL * WIDTH + cx * CELL
            n = changed[p] + changed[p + 1] + changed[p + WIDTH] + changed[p + WIDTH + 1]
            if n >= 2:
                cells.add((cx, cy))
    return cells


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


def overlaps(boxes: list[list[Any]] | None, offset_ms: int, bbox: Box | None,
             window_ms: float) -> bool:
    """Whether a detection at ``offset_ms`` with ``bbox`` (normalized; None = no box, any
    motion counts) overlaps motion within ``window_ms``."""
    for at, regions in boxes or []:
        if abs(at - offset_ms) > window_ms:
            continue
        if bbox is None:
            return True
        x, y, w, h = bbox
        for rx, ry, rw, rh in regions:
            if x < rx + rw and rx < x + w and y < ry + rh and ry < y + h:
                return True
    return False
