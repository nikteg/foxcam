// The mask editor's geometry: boxes in normalized picture coordinates (0..1), and what a
// press hits or a dragged edge snaps to, measured in screen pixels.

import type { Rect } from "../api";

/** Normalized (0..1) rectangle; independent of the stream resolution. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Point {
  x: number;
  y: number;
}

/** The picture's size on screen (zoom included). */
export interface Size {
  width: number;
  height: number;
}

export type Corner = "nw" | "ne" | "sw" | "se";
export const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];

/** No box gets smaller than this: one that vanished could not be grabbed again. */
export const MIN_SIZE = 0.01;

export const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
export const toBox = ([x, y, w, h]: Rect): Box => ({ x, y, w, h });
export const toRect = (b: Box): Rect => [b.x, b.y, b.w, b.h];

/** How far (px) a box's handles sit outside it: a box smaller than `min` keeps its
 *  middle free for moving. */
export function handleOffset(b: Box, size: Size, min: number): Point {
  return {
    x: Math.max(0, (min - b.w * size.width) / 2),
    y: Math.max(0, (min - b.h * size.height) / 2),
  };
}

/** Where a corner's handle is, in px from the picture's top left. */
export function handlePosition(b: Box, corner: Corner, size: Size, min: number): Point {
  const out = handleOffset(b, size, min);
  const west = corner.includes("w");
  const north = corner.includes("n");
  return {
    x: (west ? b.x : b.x + b.w) * size.width + (west ? -out.x : out.x),
    y: (north ? b.y : b.y + b.h) * size.height + (north ? -out.y : out.y),
  };
}

/** What a press at `p` takes hold of, with room for a finger (`touch`): the selected
 *  box's handles, then the selected box, then the smallest box near it (a small box
 *  inside a big one stays reachable). */
export function hitTest(
  boxes: Box[],
  selected: number,
  p: Point,
  size: Size,
  touch: boolean,
  handleMin: number,
): { index: number; corner?: Corner } | null {
  const slop = touch ? 16 : 4;
  const reach = touch ? 24 : 8;
  const px = p.x * size.width;
  const py = p.y * size.height;
  const inside = (b: Box, pad: number) =>
    px >= b.x * size.width - pad &&
    px <= (b.x + b.w) * size.width + pad &&
    py >= b.y * size.height - pad &&
    py <= (b.y + b.h) * size.height + pad;
  const box = boxes[selected];
  if (box) {
    const out = handleOffset(box, size, handleMin);
    // A small box's handles are outside it: a press on the box itself moves it.
    const pushed = out.x > 0 || out.y > 0;
    if (!(pushed && inside(box, 0))) {
      let best: { corner: Corner; dist: number } | undefined;
      for (const corner of CORNERS) {
        const c = handlePosition(box, corner, size, handleMin);
        const dist = Math.hypot(px - c.x, py - c.y);
        if (dist <= reach && (!best || dist < best.dist)) best = { corner, dist };
      }
      if (best) return { index: selected, corner: best.corner };
    }
    if (inside(box, slop)) return { index: selected };
  }
  let found = -1;
  boxes.forEach((b, i) => {
    const best = boxes[found];
    if (inside(b, slop) && (!best || b.w * b.h < best.w * best.h)) found = i;
  });
  return found >= 0 ? { index: found } : null;
}

/** The edges a box can snap to: the picture's and the other boxes'. */
export function snapLines(boxes: Box[], except: number): { xs: number[]; ys: number[] } {
  const xs = [0, 1];
  const ys = [0, 1];
  boxes.forEach((b, i) => {
    if (i === except) return;
    xs.push(b.x, b.x + b.w);
    ys.push(b.y, b.y + b.h);
  });
  return { xs, ys };
}

/** The line nearest to `v` within `tolerance`, else `v`. */
export function snapValue(v: number, lines: number[], tolerance: number): number {
  let best = v;
  let gap = tolerance;
  for (const line of lines) {
    const d = Math.abs(line - v);
    if (d < gap) {
      gap = d;
      best = line;
    }
  }
  return best;
}

/** How far to shift a span [a, b] so that its nearest edge lands on a line (0: none). */
export function snapShift(a: number, b: number, lines: number[], tolerance: number): number {
  let shift = 0;
  let gap = tolerance;
  for (const line of lines) {
    for (const edge of [a, b]) {
      const d = line - edge;
      if (Math.abs(d) < gap) {
        gap = Math.abs(d);
        shift = d;
      }
    }
  }
  return shift;
}
