// The sun's elevation through the day where a camera is, with its target detections on the
// curve: where the wrong ones sit shows what to set the camera's sun limit to (drag the
// line, or type the value next to it). Server: pipeline/daylight.py.
import { tokens } from "@fluentui/react-components";
import { useEffect, useRef, useState, type PointerEvent } from "react";

import type { SunChartData, SunDetection } from "../api";
import { decimal, localTime } from "../format";
import { useI18n } from "../i18n";

export const HEIGHT = 260;
const PAD = { top: 12, right: 44, bottom: 26, left: 36 };
/** Pointer this close to the limit line (px) grabs it. */
const GRAB = 18;
const STEP = 0.5;
/** Pointer this close to a detection (px) shows it. */
const HIT = 10;

/** Marks: identity by shape as well as colour (wrong = diamond). */
const COLOR = {
  wrong: tokens.colorPaletteRedBackground3,
  correct: tokens.colorPaletteGreenBackground3,
  unmarked: tokens.colorBrandBackground,
};

export type Kind = keyof typeof COLOR;
export const kindOf = (d: SunDetection): Kind =>
  d.feedback === false ? "wrong" : d.feedback === true ? "correct" : "unmarked";

/** Minutes since local midnight. */
const minuteOfDay = (date: Date) => date.getHours() * 60 + date.getMinutes();

/** The pointer's height in the chart. */
const pointerY = (e: PointerEvent<SVGElement>) =>
  e.clientY - (e.currentTarget.ownerSVGElement?.getBoundingClientRect().top ?? 0);

/** (minute, elevation) in time order, ending at midnight again (so curves reach it). */
function byMinute(values: Map<number, number>) {
  const points = [...values].toSorted((a, b) => a[0] - b[0]);
  const first = points[0];
  if (first && first[0] === 0) points.push([1440, first[1]]);
  return points;
}

export const snap = (v: number) => Math.round(v / STEP) * STEP;

export function SunChart({
  data,
  limit,
  onLimit,
}: {
  data: SunChartData;
  /** null: no limit (no line). */
  limit: number | null;
  onLimit: (value: number) => void;
}) {
  const { t } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<SunDetection | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // The sun by time of day (local): its range over the period (a band) and the last 24 h
  // (a line). Many overlapping days drawn as lines read as a jagged smear.
  const start = Date.parse(data.start);
  const perDay = Math.round(1440 / data.step_minutes);
  const low = new Map<number, number>();
  const high = new Map<number, number>();
  const today = new Map<number, number>();
  data.sun.forEach((e, i) => {
    const m = minuteOfDay(new Date(start + i * data.step_minutes * 60_000));
    low.set(m, Math.min(low.get(m) ?? Infinity, e));
    high.set(m, Math.max(high.get(m) ?? -Infinity, e));
    if (i >= data.sun.length - perDay) today.set(m, e);
  });

  const values = [...data.sun, ...data.detections.map((d) => d.elevation), limit ?? 0];
  const lo = Math.floor(Math.min(...values) / 10) * 10;
  const hi = Math.ceil(Math.max(...values) / 10) * 10;
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (minute: number) => PAD.left + (minute / 1440) * plotW;
  const y = (elev: number) => PAD.top + ((hi - elev) / (hi - lo || 1)) * plotH;
  const elevAt = (py: number) => hi - ((py - PAD.top) / plotH) * (hi - lo);
  const clampLimit = (v: number) => Math.min(hi, Math.max(lo, snap(v)));

  const grab = (e: PointerEvent<SVGRectElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    onLimit(clampLimit(elevAt(pointerY(e))));
  };
  const drag = (e: PointerEvent<SVGRectElement>) => {
    if (dragging) onLimit(clampLimit(elevAt(pointerY(e))));
  };

  const ticks: number[] = [];
  for (let v = lo; v <= hi; v += 10) ticks.push(v);
  const hours = width < 480 ? [0, 6, 12, 18, 24] : [0, 3, 6, 9, 12, 15, 18, 21, 24];
  // Each detection where it is drawn. One path per look (a few thousand SVG elements with
  // their own handlers made the drawer slow to open), wrong ones on top: they are what the
  // limit is for.
  const placed = data.detections.map((d) => ({
    d,
    kind: kindOf(d),
    cx: x(minuteOfDay(new Date(d.ts))),
    cy: y(d.elevation),
    ignored: limit !== null && d.elevation < limit,
  }));
  const marks = (["unmarked", "correct", "wrong"] as const).flatMap((kind) =>
    [true, false].map((ignored) => ({
      kind,
      ignored,
      d: placed
        .filter((p) => p.kind === kind && p.ignored === ignored)
        .map((p) => mark(kind, p.cx, p.cy))
        .join(""),
    })),
  );
  /** The detection under the pointer (the nearest within reach), for the tooltip. */
  const pick = (e: PointerEvent<SVGSVGElement>) => {
    // Not while moving the limit line (its grab strip is on top).
    if (dragging || (e.target as Element).classList.contains("sun-grab")) {
      setHover(null);
      return;
    }
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;
    let best: SunDetection | null = null;
    let reach = HIT * HIT;
    for (const p of placed) {
      const dist = (p.cx - px) ** 2 + (p.cy - py) ** 2;
      if (dist < reach) {
        reach = dist;
        best = p.d;
      }
    }
    setHover(best);
  };

  return (
    <div ref={box} className="sun-chart">
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          aria-label={t("sun.chartLabel", { n: data.detections.length })}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={() => setHover(null)}
        >
          {limit !== null && (
            <rect
              className="sun-ignored"
              x={PAD.left}
              y={y(limit)}
              width={plotW}
              height={Math.max(0, PAD.top + plotH - y(limit))}
            />
          )}
          {ticks.map((v) => (
            <g key={v}>
              <line
                className={v === 0 ? "sun-horizon" : "sun-grid"}
                x1={PAD.left}
                x2={PAD.left + plotW}
                y1={y(v)}
                y2={y(v)}
              />
              <text className="sun-axis" x={PAD.left - 6} y={y(v)} dy="0.32em" textAnchor="end">
                {v}°
              </text>
            </g>
          ))}
          {hours.map((h) => (
            <text
              key={h}
              className="sun-axis"
              x={x(h * 60)}
              y={HEIGHT - 8}
              textAnchor={h === 0 ? "start" : h === 24 ? "end" : "middle"}
            >
              {String(h % 24).padStart(2, "0")}
            </text>
          ))}
          <path className="sun-band" d={band(byMinute(high), byMinute(low), x, y)} />
          <path className="sun-curve" d={smooth(byMinute(today).map(([m, e]) => [x(m), y(e)]))} />
          {marks.map(({ kind, ignored, d }) => (
            <path
              key={`${kind}${ignored}`}
              className="sun-dot"
              d={d}
              style={{
                fill: ignored ? tokens.colorNeutralBackground1 : COLOR[kind],
                stroke: COLOR[kind],
              }}
            />
          ))}
          {limit !== null && (
            <g className="sun-limit">
              <line x1={PAD.left} x2={PAD.left + plotW} y1={y(limit)} y2={y(limit)} />
              <circle cx={PAD.left + plotW} cy={y(limit)} r={6} />
              <text x={PAD.left + plotW + 10} y={y(limit)} dy="0.32em">
                {decimal(limit)}°
              </text>
              <rect
                className={`sun-grab${dragging ? " dragging" : ""}`}
                x={PAD.left}
                y={y(limit) - GRAB}
                width={plotW + PAD.right}
                height={GRAB * 2}
                onPointerDown={grab}
                onPointerMove={drag}
                onPointerUp={() => setDragging(false)}
                onPointerCancel={() => setDragging(false)}
              />
            </g>
          )}
        </svg>
      )}
      {hover && width > 0 && (
        <div
          className="sun-tooltip"
          style={{
            left: Math.min(Math.max(x(minuteOfDay(new Date(hover.ts))), 90), width - 90),
            top: y(hover.elevation),
          }}
        >
          <strong>{hover.label}</strong> {localTime(hover.ts)}
          <br />
          {t("sun.elevation", { value: decimal(hover.elevation) })}
          {" – "}
          {t(`sun.kind.${kindOf(hover)}`)}
        </div>
      )}
    </div>
  );
}

/** A detection's mark at (cx, cy) as path data: a diamond for wrong ones, else a dot. */
function mark(kind: Kind, cx: number, cy: number) {
  if (kind === "wrong") return `M${cx},${cy - 5.5}l5.5,5.5l-5.5,5.5l-5.5,-5.5z`;
  return `M${cx - 4},${cy}a4,4 0 1,0 8,0a4,4 0 1,0 -8,0z`;
}

/** A smooth curve through the points (Catmull-Rom as cubic Béziers). */
function smooth(points: [number, number][], move = true) {
  return points
    .map(([px, py], i) => {
      if (i === 0) return move ? `M${px},${py}` : `L${px},${py}`;
      const [ax, ay] = points[i - 2] ?? points[i - 1]!;
      const [bx, by] = points[i - 1]!;
      const [dx, dy] = points[i + 1] ?? [px, py];
      return `C${bx + (px - ax) / 6},${by + (py - ay) / 6} ${px - (dx - bx) / 6},${py - (dy - by) / 6} ${px},${py}`;
    })
    .join("");
}

/** The area between two curves of (minute, elevation): along the top, back along the
 * bottom. */
function band(
  top: [number, number][],
  bottom: [number, number][],
  x: (minute: number) => number,
  y: (elevation: number) => number,
) {
  if (top.length === 0) return "";
  const scale = (points: [number, number][]) =>
    points.map(([m, e]): [number, number] => [x(m), y(e)]);
  return `${smooth(scale(top))}${smooth(scale(bottom).toReversed(), false)}z`;
}
