import { SkeletonItem } from "@fluentui/react-components";
import { useState } from "react";

import type { PlayerDetection } from "../api";
import { DetectionBoxes, PictureBoxes } from "./Boxes";

/** A picture that keeps its room while it loads (`width`/`height`, e.g. the recording's
 *  size; else the box from `className`, 16:9 by default), shows a skeleton meanwhile and
 *  fades in. `preview`: a smaller copy that is likely cached already (the list's
 *  thumbnail), shown until then. `keepPrevious`: for a picture that is reloaded (a live
 *  snapshot), the next one fades in over the last one instead of after a skeleton.
 *  `boxes`: detections drawn over the picture once it has loaded. */
export function FrameImage({
  src,
  preview,
  width,
  height,
  alt,
  className,
  lazy,
  keepPrevious,
  boxes,
}: {
  src: string;
  preview?: string;
  width?: number | null;
  height?: number | null;
  alt: string;
  className?: string;
  lazy?: boolean;
  keepPrevious?: boolean;
  boxes?: PlayerDetection[];
}) {
  const [loaded, setLoaded] = useState<string>();
  const [failed, setFailed] = useState<string>();
  // The loaded picture's own aspect ratio, for the boxes (it may be cropped to fit).
  const [aspect, setAspect] = useState<number>();
  // The last picture that finished fading in. What lies underneath (skeleton, preview,
  // the previous picture) stays until then: taken away at load, the fade would start
  // from the black background.
  const [shown, setShown] = useState<string>();
  const covered = shown === src;
  const previous = keepPrevious && !covered ? shown : undefined;
  const under = !covered && !previous;
  return (
    // Spans: it may sit in a button (a card's picture).
    <span
      className={`frame-image ${keepPrevious ? "crossfade" : ""} ${className ?? ""}`}
      style={{ aspectRatio: width && height ? `${width} / ${height}` : undefined }}
    >
      {under && failed !== src && <SkeletonItem as="span" className="frame-image-skeleton" />}
      {under && preview && (
        <img key={preview} className="frame-image-preview" src={preview} alt="" />
      )}
      {previous && <img key={previous} className="loaded" src={previous} alt="" />}
      {/* Keyed: another picture starts hidden instead of showing the previous one meanwhile. */}
      <img
        key={src}
        className={loaded === src ? "loaded" : undefined}
        src={src}
        alt={alt}
        loading={lazy ? "lazy" : undefined}
        onLoad={(e) => {
          setLoaded(src);
          const img = e.currentTarget;
          if (img.naturalWidth) setAspect(img.naturalWidth / img.naturalHeight);
        }}
        onError={() => setFailed(src)}
        onAnimationEnd={() => setShown(src)}
      />
      {!!boxes?.length && loaded === src && aspect && (
        <PictureBoxes aspect={aspect} fit="cover">
          <DetectionBoxes detections={boxes} />
        </PictureBoxes>
      )}
    </span>
  );
}
