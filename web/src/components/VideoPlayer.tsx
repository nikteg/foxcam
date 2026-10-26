// The app's video player: Vidstack plays the video and keeps its state (keyboard
// shortcuts, gestures, hiding the controls); the controls are our own, made of Fluent UI
// parts on a dark theme, with a timeline that previews the server's thumbnail sprites
// (WebVTT) and can show markers (e.g. where things moved).
import {
  FluentProvider,
  Button,
  Menu,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Slider,
  Spinner,
} from "@fluentui/react-components";
import {
  FullScreenMaximize20Regular,
  FullScreenMinimize20Regular,
  Pause20Filled,
  Play20Filled,
  Play28Filled,
  Speaker220Regular,
  SpeakerMute20Regular,
} from "@fluentui/react-icons";
import {
  Controls,
  Gesture,
  MediaPlayer,
  MediaProvider,
  Poster,
  Time,
  TimeSlider,
  useMediaPlayer,
  useMediaRemote,
  useMediaState,
  type MediaPlayerInstance,
} from "@vidstack/react";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import "@vidstack/react/player/styles/base.css";

import type { MotionFrame } from "../api";
import { useI18n } from "../i18n";
import { darkTheme } from "../theme";
import { useMediaQuery } from "../useMediaQuery";

export type PlayerHandle = MediaPlayerInstance;

/** A stretch of the video to mark on the timeline, in seconds. */
export type Marker = { from: number; to: number };

const RATES = [0.5, 1, 1.5, 2, 4, 8];
/** A double tap at the side seeks this far when there is no marker to jump to. */
const SEEK_S = 10;

export function VideoPlayer({
  src,
  title,
  poster,
  thumbnails,
  startAt,
  autoPlay = false,
  playerRef,
  overlay,
  markers,
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
  /** Marked on the timeline; a double tap at the sides jumps between them. */
  markers?: Marker[];
}) {
  const own = useRef<MediaPlayerInstance>(null);
  const ref = playerRef ?? own;
  // One player for every item a drawer shows (remounting it made the drawer jump): another
  // moment in the same video seeks here; a new video seeks once loaded (below).
  useEffect(() => {
    const player = ref.current;
    if (startAt !== undefined && player?.state.canPlay) player.currentTime = startAt;
  }, [ref, startAt]);
  return (
    <MediaPlayer
      ref={ref}
      className="video-player"
      title={title}
      // Our URLs have no file extension: say what it is.
      src={{ src, type: "video/mp4" }}
      playsInline
      autoPlay={autoPlay}
      // "visible" never fired inside the drawers. One player at a time, and only the
      // metadata is fetched up front.
      load="eager"
      posterLoad="eager"
      onLoadedMetadata={() => {
        if (startAt && ref.current) ref.current.currentTime = startAt;
      }}
    >
      <MediaProvider>
        {poster && <Poster className="vds-poster player-poster" src={poster} alt="" />}
      </MediaProvider>
      {overlay}
      <Gestures markers={markers} />
      <PlayerControls thumbnails={thumbnails} markers={markers} />
    </MediaPlayer>
  );
}

/** Taps and clicks on the picture. Mouse: click plays or pauses, double click goes
 * fullscreen. Touch: a tap shows the controls, a double tap at the sides jumps to the
 * previous / next marker (or 10 s). */
function Gestures({ markers }: { markers?: Marker[] }) {
  const player = useMediaPlayer();
  const coarse = useMediaQuery("(pointer: coarse)");
  const jump = (direction: -1 | 1) => (_: unknown, e: Event) => {
    // The video's own clock: the player's lags behind a seek while paused.
    const now = player?.el?.querySelector("video")?.currentTime ?? player?.currentTime ?? 0;
    const starts = (markers ?? []).map((m) => m.from);
    // Backwards skips the marker playing now (a second tap goes one further).
    const marker =
      direction > 0 ? starts.find((t) => t > now + 0.5) : starts.findLast((t) => t < now - 1);
    if (!player) return;
    // Seeking here also for the plain 10 s: Vidstack's own would count from its lagging clock.
    e.preventDefault();
    const end = player.state.duration || Infinity;
    player.currentTime = Math.min(Math.max(marker ?? now + direction * SEEK_S, 0), end);
  };
  return (
    <>
      <Gesture
        className="player-gesture"
        event="pointerup"
        action={coarse ? "toggle:controls" : "toggle:paused"}
      />
      {!coarse && (
        <Gesture className="player-gesture" event="dblpointerup" action="toggle:fullscreen" />
      )}
      {coarse && (
        <>
          <Gesture
            className="player-gesture player-gesture-side start"
            event="dblpointerup"
            action={`seek:${-SEEK_S}`}
            onWillTrigger={jump(-1)}
          />
          <Gesture
            className="player-gesture player-gesture-side end"
            event="dblpointerup"
            action={`seek:${SEEK_S}`}
            onWillTrigger={jump(1)}
          />
        </>
      )}
    </>
  );
}

function PlayerControls({ thumbnails, markers }: { thumbnails?: string; markers?: Marker[] }) {
  const { t } = useI18n();
  const remote = useMediaRemote();
  const paused = useMediaState("paused");
  const started = useMediaState("started");
  const waiting = useMediaState("waiting");
  const muted = useMediaState("muted");
  const volume = useMediaState("volume");
  const fullscreen = useMediaState("fullscreen");
  const canFullscreen = useMediaState("canFullscreen");
  const duration = useMediaState("duration");
  const coarse = useMediaQuery("(pointer: coarse)");
  return (
    // Always dark: the controls sit on the picture.
    <FluentProvider theme={darkTheme} className="player-ui">
      {waiting && <Spinner className="player-spinner" size="medium" />}
      {!started && paused && !waiting && (
        <Button
          className="player-start"
          shape="circular"
          size="large"
          appearance="primary"
          icon={<Play28Filled />}
          aria-label={t("player.play")}
          onClick={() => remote.play()}
        />
      )}
      <Controls.Root className="player-controls" hideDelay={2000}>
        <Controls.Group className="player-timeline-row">
          <TimeSlider.Root className="player-timeline">
            <TimeSlider.Track className="player-track">
              <TimeSlider.Progress className="player-track-progress" />
              {duration > 0 && markers && (
                <span className="player-markers" aria-hidden="true">
                  {markers.map((m) => (
                    <span
                      key={m.from}
                      className="player-marker"
                      style={{
                        left: `${(m.from / duration) * 100}%`,
                        width: `${(Math.max(m.to - m.from, 0) / duration) * 100}%`,
                      }}
                    />
                  ))}
                </span>
              )}
              <TimeSlider.TrackFill className="player-track-fill" />
            </TimeSlider.Track>
            <TimeSlider.Thumb className="player-thumb" />
            <TimeSlider.Preview className="player-preview" noClamp={false}>
              {thumbnails && (
                <TimeSlider.Thumbnail.Root src={thumbnails} className="player-preview-image">
                  <TimeSlider.Thumbnail.Img />
                </TimeSlider.Thumbnail.Root>
              )}
              <TimeSlider.Value className="player-preview-time" />
            </TimeSlider.Preview>
          </TimeSlider.Root>
        </Controls.Group>
        <Controls.Group className="player-bar">
          <Button
            appearance="transparent"
            icon={paused ? <Play20Filled /> : <Pause20Filled />}
            aria-label={paused ? t("player.play") : t("player.pause")}
            onClick={() => remote.togglePaused()}
          />
          <Button
            appearance="transparent"
            icon={muted || volume === 0 ? <SpeakerMute20Regular /> : <Speaker220Regular />}
            aria-label={muted ? t("player.unmute") : t("player.mute")}
            onClick={() => remote.toggleMuted()}
          />
          {/* Phones set the volume with their buttons (iOS ignores it here). */}
          {!coarse && (
            <Slider
              className="player-volume"
              size="small"
              min={0}
              max={100}
              value={muted ? 0 : Math.round(volume * 100)}
              aria-label={t("player.volume")}
              onChange={(_, d) => {
                remote.changeVolume(d.value / 100);
                if (muted && d.value > 0) remote.unmute();
              }}
            />
          )}
          <span className="player-time">
            <Time type="current" /> / <Time type="duration" />
          </span>
          <span className="player-bar-end">
            <SpeedMenu />
            {canFullscreen && (
              <Button
                appearance="transparent"
                icon={
                  fullscreen ? <FullScreenMinimize20Regular /> : <FullScreenMaximize20Regular />
                }
                aria-label={fullscreen ? t("player.exitFullscreen") : t("player.fullscreen")}
                onClick={() => remote.toggleFullscreen()}
              />
            )}
          </span>
        </Controls.Group>
      </Controls.Root>
    </FluentProvider>
  );
}

/** The playback speed, always in the bar. Inline: in the player, so it shows in
 * fullscreen too. */
function SpeedMenu() {
  const { t } = useI18n();
  const remote = useMediaRemote();
  const rate = useMediaState("playbackRate");
  return (
    <Menu
      inline
      positioning="above-end"
      checkedValues={{ rate: [String(rate)] }}
      onCheckedValueChange={(_, d) => remote.changePlaybackRate(Number(d.checkedItems[0]))}
    >
      <MenuTrigger disableButtonEnhancement>
        <Button appearance="transparent" className="player-speed" aria-label={t("player.speed")}>
          {rate}×
        </Button>
      </MenuTrigger>
      <MenuPopover className="player-speed-menu">
        <MenuList>
          {RATES.toReversed().map((r) => (
            <MenuItemRadio key={r} name="rate" value={String(r)}>
              {r}×
            </MenuItemRadio>
          ))}
        </MenuList>
      </MenuPopover>
    </Menu>
  );
}

/** Frames closer than this (ms) are one stretch of motion on the timeline. */
const MARKER_GAP_MS = 1500;

/** Where things moved, as timeline markers. */
export function motionMarkers(frames: MotionFrame[]): Marker[] {
  const spans: Marker[] = [];
  for (const [at] of frames) {
    const last = spans.at(-1);
    if (last && at / 1000 - last.to <= MARKER_GAP_MS / 1000) last.to = (at + FRAME_MS) / 1000;
    else spans.push({ from: at / 1000, to: (at + FRAME_MS) / 1000 });
  }
  return spans;
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
