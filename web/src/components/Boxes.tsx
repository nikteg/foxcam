// Boxes over a picture, a video's or a still's: where things moved, what a detector found.
// Drawn here rather than burned into the picture, so they can be turned off and show what
// is known now (a detection marked wrong). Only notification images have them burned in.
import type { CSSProperties, ReactNode } from "react";

import type { Detection, DetectionEvent, PlayerDetection, Rect } from "../api";
import { percent } from "../format";

/** A box the size and place of the picture inside its container, for boxes in picture
 *  coordinates (0..1). The container shows the picture with `object-fit: fit`; `aspect`
 *  is the picture's width / height. */
export function PictureBoxes({
  aspect,
  fit = "contain",
  className,
  children,
}: {
  aspect: number;
  fit?: "contain" | "cover";
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={`picture-boxes ${fit} ${className ?? ""}`} aria-hidden="true">
      <span className="picture-boxes-frame" style={{ "--aspect": aspect } as CSSProperties}>
        {children}
      </span>
    </span>
  );
}

const place = ([x, y, w, h]: Rect): CSSProperties => ({
  left: `${x * 100}%`,
  top: `${y * 100}%`,
  width: `${w * 100}%`,
  height: `${h * 100}%`,
});

/** Boxes around what moved. */
export function MotionBoxes({ boxes }: { boxes: Rect[] }) {
  return boxes.map((b) => <span key={`${b[0]},${b[1]}`} className="motion-box" style={place(b)} />);
}

/** Boxes around what detectors found, with label and confidence. Those that are no match
 *  (not a target, standing still, marked wrong) are drawn faint. */
export function DetectionBoxes({ detections }: { detections: PlayerDetection[] }) {
  return detections.map((d) => (
    <span
      key={d.id}
      className={`detection-box ${d.match ? "match" : ""}`}
      // Its label goes inside a box at the top edge, where there is no room above it.
      data-label={d.bbox[1] < 0.08 ? "inside" : undefined}
      style={place(d.bbox)}
    >
      <span className="detection-box-label">
        {d.label} {percent(d.confidence)}
      </span>
    </span>
  ));
}

/** The box over an event's thumbnail (its best detection's), if it is not burned in. */
export function eventThumbBox(e: DetectionEvent): PlayerDetection[] {
  if (!e.thumb_box || e.best_detection_id === null) return [];
  return [
    {
      id: e.best_detection_id,
      at_ms: 0,
      bbox: e.thumb_box,
      label: e.label,
      confidence: e.max_confidence,
      detector_id: null,
      match: e.feedback !== "wrong",
    },
  ];
}

/** Detections from the API (with a box) as the player draws them, in their recording's
 *  time; `segmentId`: only those of that recording. */
export function playerDetections(list: Detection[] | undefined, segmentId?: number) {
  return (list ?? []).flatMap((d): PlayerDetection[] =>
    d.bbox && (segmentId === undefined || d.segment_id === segmentId)
      ? [
          {
            id: d.id,
            at_ms: d.offset_ms,
            bbox: d.bbox,
            label: d.label,
            confidence: d.confidence,
            detector_id: d.detector_id,
            match: d.match,
          },
        ]
      : [],
  );
}
