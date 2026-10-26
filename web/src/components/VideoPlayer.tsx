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
  ArrowRepeatAll20Filled,
  ArrowRepeatAll20Regular,
  FullScreenMaximize20Regular,
  FullScreenMinimize20Regular,
  Pause20Filled,
  Play20Filled,
  Play28Filled,
  Run20Filled,
  Run20Regular,
  Speaker220Regular,
  SpeakerMute20Regular,
} from "@fluentui/react-icons";
import {
  Controls,
  MediaPlayer,
  MediaProvider,
  Poster,
  Time,
  TimeSlider,
  useMediaPlayer,
  useActiveThumbnail,
  useMediaRemote,
  useMediaState,
  useThumbnails,
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
  motion,
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
  /** Where things moved: boxes on the picture and marks on the timeline, with a button in
   * the bar to hide them (shown by default). */
  motion?: MotionFrame[];
}) {
  const [showMotion, setShowMotion] = useState(true);
  const hasMotion = !!motion?.length;
  const motionOn = hasMotion && showMotion;
  const allMarkers = motionOn ? [...(markers ?? []), ...motionMarkers(motion)] : markers;
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
      {motionOn && <MotionOverlay frames={motion} />}
      <PictureTaps markers={allMarkers} />
      <PlayerControls
        thumbnails={thumbnails}
        markers={allMarkers}
        motion={
          hasMotion ? { on: showMotion, toggle: () => setShowMotion(!showMotion) } : undefined
        }
      />
    </MediaPlayer>
  );
}

/** How soon a second tap counts as a double tap (ms). */
const DOUBLE_TAP_MS = 300;

/** Taps and clicks on the picture, handled right away (Vidstack's gestures wait to tell a
 * click from a double click). Mouse: a click plays or pauses. Touch: a tap shows or hides
 * the controls; a second tap at the sides jumps to the previous / next marker (or 10 s). */
function PictureTaps({ markers }: { markers?: Marker[] }) {
  const player = useMediaPlayer();
  const remote = useMediaRemote();
  const lastTap = useRef(0);
  const jump = (direction: -1 | 1) => {
    if (!player) return;
    // The video's own clock: the player's lags behind a seek while paused.
    const now = player.el?.querySelector("video")?.currentTime ?? player.currentTime;
    const starts = (markers ?? []).map((m) => m.from);
    // Backwards skips the marker playing now (another double tap goes one further).
    const marker =
      direction > 0 ? starts.find((t) => t > now + 0.5) : starts.findLast((t) => t < now - 1);
    const end = player.state.duration || Infinity;
    remote.seek(Math.min(Math.max(marker ?? now + direction * SEEK_S, 0), end));
  };
  return (
    // Pointer events only; the keyboard works on the player (Vidstack's shortcuts).
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="player-surface"
      onPointerUp={(e) => {
        if (e.button !== 0) return;
        if (e.pointerType === "mouse") {
          remote.togglePaused(e.nativeEvent);
          return;
        }
        const now = performance.now();
        const second = now - lastTap.current < DOUBLE_TAP_MS;
        lastTap.current = second ? 0 : now;
        const box = e.currentTarget.getBoundingClientRect();
        const x = (e.clientX - box.left) / box.width;
        if (second && (x < 1 / 3 || x > 2 / 3)) jump(x < 1 / 3 ? -1 : 1);
        else if (!second) remote.toggleControls(e.nativeEvent);
      }}
    />
  );
}

function PlayerControls({
  thumbnails,
  markers,
  motion,
}: {
  thumbnails?: string;
  markers?: Marker[];
  /** The "show motion" toggle, when the video has motion to show. */
  motion?: { on: boolean; toggle: () => void };
}) {
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
  const loop = useMediaState("loop");
  const coarse = useMediaQuery("(pointer: coarse)");
  // While the timeline is dragged: where to (percent), shown as a picture over the video.
  const [scrub, setScrub] = useState<number | null>(null);
  return (
    // Always dark: the controls sit on the picture.
    <FluentProvider theme={darkTheme} className="player-ui">
      {thumbnails && scrub !== null && duration > 0 && (
        <ScrubPicture src={thumbnails} time={(scrub / 100) * duration} />
      )}
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
          <TimeSlider.Root
            className="player-timeline"
            onDragStart={(value) => setScrub(value)}
            onDragValueChange={(value) => setScrub(value)}
            onDragEnd={() => setScrub(null)}
          >
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
          {/* Each group on its own dark chip, readable on any picture. */}
          <span className="player-chip">
            <Button
              appearance="transparent"
              icon={paused ? <Play20Filled /> : <Pause20Filled />}
              aria-label={paused ? t("player.play") : t("player.pause")}
              onClick={() => remote.togglePaused()}
            />
          </span>
          <span className="player-chip">
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
          </span>
          <span className="player-chip player-time">
            <Time type="current" />
            <span className="player-time-sep">/</span>
            <Time type="duration" />
          </span>
          <span className="player-chip player-bar-end">
            {motion && (
              <Button
                appearance="transparent"
                className={motion.on ? "player-on" : undefined}
                icon={motion.on ? <Run20Filled /> : <Run20Regular />}
                aria-label={t("recordings.showMotion")}
                title={t("recordings.showMotion")}
                aria-pressed={motion.on}
                onClick={motion.toggle}
              />
            )}
            <Button
              appearance="transparent"
              className={loop ? "player-on" : undefined}
              icon={loop ? <ArrowRepeatAll20Filled /> : <ArrowRepeatAll20Regular />}
              aria-label={t("player.loop")}
              aria-pressed={loop}
              onClick={() => remote.userPrefersLoopChange(!loop)}
            />
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

/** While scrubbing: the thumbnail of that moment over the whole picture (a live preview,
 * instead of the video catching up with each seek). The thumbnails are tiles of a sprite:
 * the sprite is scaled so its tile fills the frame. */
function ScrubPicture({ src, time }: { src: string; time: number }) {
  const thumbs = useThumbnails(src);
  const active = useActiveThumbnail(thumbs, time);
  const [spriteWidth, setSpriteWidth] = useState(0);
  if (!active) return null;
  const { width: w, height: h, coords } = active;
  const tile = w && h && coords;
  return (
    <div className="player-scrub" aria-hidden="true">
      <div className="player-scrub-frame" style={tile ? { aspectRatio: `${w} / ${h}` } : undefined}>
        <img
          src={active.url.href}
          alt=""
          onLoad={(e) => setSpriteWidth(e.currentTarget.naturalWidth)}
          style={
            tile
              ? {
                  position: "absolute",
                  width: `${(spriteWidth / w) * 100}%`,
                  maxWidth: "none",
                  left: `${(-coords.x / w) * 100}%`,
                  top: `${(-coords.y / h) * 100}%`,
                  visibility: spriteWidth ? "visible" : "hidden",
                }
              : { width: "100%", height: "100%", objectFit: "contain" }
          }
        />
      </div>
    </div>
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
