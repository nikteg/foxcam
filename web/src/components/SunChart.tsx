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

  // One line per day, in local time (a new line where the time of day starts over).
  const start = Date.parse(data.start);
  const days: [number, number][][] = [];
  for (let i = 0, prev = Infinity; i < data.sun.length; i++) {
    const m = minuteOfDay(new Date(start + i * data.step_minutes * 60_000));
    if (m < prev) days.push([]);
    days.at(-1)?.push([m, data.sun[i] ?? 0]);
    prev = m;
  }

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
  // Wrong ones on top: they are what the limit is for.
  const order: Kind[] = ["unmarked", "correct", "wrong"];
  const dots = data.detections.toSorted(
    (a, b) => order.indexOf(kindOf(a)) - order.indexOf(kindOf(b)),
  );

  return (
    <div ref={box} className="sun-chart">
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          aria-label={t("sun.chartLabel", { n: data.detections.length })}
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
          {/* One path for all days: its stroke is painted once, so where days overlap the
              translucency doesn't add up into bands. */}
          <path
            className="sun-curve"
            d={days
              .map((points) =>
                points.map(([m, e], j) => `${j ? "L" : "M"}${x(m)},${y(e)}`).join(""),
              )
              .join("")}
          />
          {dots.map((d) => {
            const kind = kindOf(d);
            const cx = x(minuteOfDay(new Date(d.ts)));
            const cy = y(d.elevation);
            const ignored = limit !== null && d.elevation < limit;
            const style = {
              fill: ignored ? tokens.colorNeutralBackground1 : COLOR[kind],
              stroke: COLOR[kind],
            };
            return (
              <g
                key={d.id}
                className="sun-dot"
                onPointerEnter={() => setHover(d)}
                onPointerLeave={() => setHover((h) => (h === d ? null : h))}
                onClick={() => setHover(d)}
              >
                <circle className="sun-hit" cx={cx} cy={cy} r={10} />
                {kind === "wrong" ? (
                  <rect
                    x={cx - 4}
                    y={cy - 4}
                    width={8}
                    height={8}
                    transform={`rotate(45 ${cx} ${cy})`}
                    style={style}
                  />
                ) : (
                  <circle cx={cx} cy={cy} r={4} style={style} />
                )}
              </g>
            );
          })}
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
