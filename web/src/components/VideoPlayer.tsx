// The app's video player (Vidstack): hover previews on the timeline from the server's
// thumbnail sprites (WebVTT), keyboard shortcuts and the same look on every browser.
import {
  MediaPlayer,
  MediaProvider,
  Menu,
  Poster,
  useMediaState,
  usePlaybackRateOptions,
  type MediaPlayerInstance,
} from "@vidstack/react";
import { DefaultVideoLayout, defaultLayoutIcons } from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import type { MotionFrame } from "../api";

import { useI18n } from "../i18n";

export type PlayerHandle = MediaPlayerInstance;

const RATES = [0.5, 1, 1.5, 2, 4, 8];

/** The playback speed, always in the control bar (instead of inside a settings menu). */
function SpeedMenu() {
  const { t } = useI18n();
  const rate = useMediaState("playbackRate");
  const options = usePlaybackRateOptions({ rates: RATES, normalLabel: null });
  return (
    <Menu.Root className="vds-menu speed-menu">
      <Menu.Button
        className="vds-menu-button vds-button speed-button"
        aria-label={t("player.speed")}
      >
        {rate}×
      </Menu.Button>
      <Menu.Items className="vds-menu-items speed-items" placement="top" offset={10}>
        <Menu.RadioGroup className="speed-options" value={options.selectedValue}>
          {options.toReversed().map((o) => (
            <Menu.Radio className="speed-option" value={o.value} onSelect={o.select} key={o.value}>
              {o.rate}×
            </Menu.Radio>
          ))}
        </Menu.RadioGroup>
      </Menu.Items>
    </Menu.Root>
  );
}

export function VideoPlayer({
  src,
  title,
  poster,
  thumbnails,
  startAt,
  autoPlay = false,
  playerRef,
  overlay,
}: {
  src: string;
  title: string;
  poster?: string | null;
  /** WebVTT thumbnails track (…/thumbnails.vtt). */
  thumbnails?: string;
  /** Seconds to start at (e.g. a detection's moment). */
  startAt?: number;
  autoPlay?: boolean;
  /** For seeking from outside (e.g. a detection's play button). */
  playerRef?: RefObject<MediaPlayerInstance | null>;
  /** Drawn over the picture (e.g. `<MotionOverlay>`), under the controls. */
  overlay?: ReactNode;
}) {
  const own = useRef<MediaPlayerInstance>(null);
  const ref = playerRef ?? own;
  return (
    <MediaPlayer
      ref={ref}
      className="video-player"
      title={title}
      // Our URLs have no file extension: say what it is.
      src={{ src, type: "video/mp4" }}
      playsInline
      autoPlay={autoPlay}
      load="visible"
      onLoadedMetadata={() => {
        if (startAt && ref.current) ref.current.currentTime = startAt;
      }}
    >
      <MediaProvider>
        {poster && <Poster className="vds-poster" src={poster} alt="" />}
      </MediaProvider>
      {overlay}
      <DefaultVideoLayout
        icons={defaultLayoutIcons}
        thumbnails={thumbnails}
        // The compact layout hides the timeline until playback starts; the drawers are
        // wide enough for the full one, so scrubbing (with previews) works right away.
        smallLayoutWhen={({ width }) => width < 400}
        // Speed is the only setting that matters here: show it instead of the menu.
        slots={{ settingsMenu: <SpeedMenu />, pipButton: null }}
      />
    </MediaPlayer>
  );
}

/** How long one frame's motion stays on screen (frames are 0.5 s apart at 2 fps). */
const FRAME_MS = 600;

/** Boxes around what moved, following the playback (inside a `VideoPlayer`). */
export function MotionOverlay({ frames }: { frames: MotionFrame[] }) {
  const time = useMediaState("currentTime");
  const width = useMediaState("width");
  const height = useMediaState("height");
  const canPlay = useMediaState("canPlay");
  // Until playback starts the poster (the recording's first moment) covers the video.
  const started = useMediaState("started");
  const box = useRef<HTMLDivElement>(null);
  // The video's own aspect ratio: the picture is letterboxed inside the player.
  const [aspect, setAspect] = useState<number | null>(null);
  useEffect(() => {
    const video = box.current?.closest(".video-player")?.querySelector("video");
    if (video?.videoWidth) setAspect(video.videoWidth / video.videoHeight);
  }, [canPlay]);
  const ms = time * 1000;
  const frame = started ? frames.findLast(([at]) => at <= ms && ms < at + FRAME_MS) : undefined;
  let picture = { left: 0, top: 0, width, height };
  if (aspect && width && height) {
    const w = Math.min(width, height * aspect);
    const h = w / aspect;
    picture = { left: (width - w) / 2, top: (height - h) / 2, width: w, height: h };
  }
  return (
    <div ref={box} className="motion-overlay" aria-hidden="true">
      <div className="motion-picture" style={picture}>
        {frame?.[1].map(([x, y, w, h]) => (
          <span
            key={`${x},${y}`}
            className="motion-box"
            style={{
              left: `${x * 100}%`,
              top: `${y * 100}%`,
              width: `${w * 100}%`,
              height: `${h * 100}%`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
