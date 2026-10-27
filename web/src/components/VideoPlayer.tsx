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
  MEDIA_KEY_SHORTCUTS,
  type MediaKeyShortcuts,
  type MediaPlayerInstance,
  type MediaRemoteControl,
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

const RATES = [0.25, 0.5, 1, 1.5, 2, 4, 8];
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
      keyShortcuts={keyShortcuts(allMarkers)}
      onLoadedMetadata={() => {
        if (startAt && ref.current) ref.current.currentTime = startAt;
      }}
    >
      <MediaProvider>
        {poster && <Poster className="vds-poster player-poster" src={poster} alt="" />}
      </MediaProvider>
      {overlay}
      {motionOn && <MotionOverlay frames={motion} />}
      <FrameClock />
      <PictureTaps markers={allMarkers} />
      <PlayerControls
        thumbnails={thumbnails}
        markers={allMarkers}
        motion={
          hasMotion
            ? { on: showMotion, toggle: () => setShowMotion(!showMotion), frames: motion }
            : undefined
        }
      />
    </MediaPlayer>
  );
}

/** The arrow keys' step when there is no marker to jump to (Vidstack's own: 5 s). */
const KEY_SEEK_S = 5;
/** Frame step before a frame interval was measured (s): the cameras' usual 15 fps. */
const DEFAULT_FRAME_S = 1 / 15;

/** How long a frame lasts in each player's video (s), as `FrameClock` measured it. */
const frameSeconds = new WeakMap<MediaPlayerInstance, number>();

/** The video's own clock: the player's lags behind a seek while paused. */
function videoTime(player: MediaPlayerInstance): number {
  return player.el?.querySelector("video")?.currentTime ?? player.currentTime;
}

/** To the previous / next marker edge (where a marked stretch starts or ends), or
 * `fallback` seconds when there is none. Backwards skips the edge just passed (pressed
 * again, it goes one further). */
function jumpToMarker(
  player: MediaPlayerInstance,
  markers: Marker[] | undefined,
  direction: -1 | 1,
  fallback: number,
) {
  const now = videoTime(player);
  const edges = [...new Set((markers ?? []).flatMap((m) => [m.from, m.to]))].toSorted(
    (a, b) => a - b,
  );
  const marker =
    direction > 0 ? edges.find((t) => t > now + 0.25) : edges.findLast((t) => t < now - 1);
  seekVideo(player, marker ?? now + direction * fallback);
}

/** Seek on the video element itself. Vidstack follows it through its events, timeline
 * included; its own seek requests left the timeline (and its clock) behind while paused,
 * and its pause request while already paused left play / pause stuck. */
function seekVideo(player: MediaPlayerInstance, seconds: number) {
  const video = player.el?.querySelector("video");
  if (!video) return;
  const end = Number.isFinite(video.duration) ? video.duration : Infinity;
  const at = Math.min(Math.max(seconds, 0), end);
  // Vidstack marks the video ended on a seek into its last second, and while ended counts
  // the time as the whole length, so seeking back left the timeline full: only playing
  // cleared it. Clear it here (its state store is not typed, hence the guard).
  if (player.state.ended && at < end - 0.01) {
    (player as unknown as { $state?: { ended?: { set(v: boolean): void } } }).$state?.ended?.set(
      false,
    );
  }
  video.currentTime = at;
}

/** Keys pressed on the timeline are the timeline's own (it moves 5 s itself). */
const onTimeline = (event: KeyboardEvent) =>
  event.target instanceof Element && !!event.target.closest("[data-media-time-slider]");

/** One frame back or forward, paused (the , and . keys). */
const stepFrame =
  (direction: -1 | 1) =>
  ({ player, remote }: { player: MediaPlayerInstance; remote: MediaRemoteControl }) => {
    const video = player.el?.querySelector("video");
    if (!video) return;
    const step = () =>
      seekVideo(
        player,
        video.currentTime + direction * (frameSeconds.get(player) ?? DEFAULT_FRAME_S),
      );
    if (video.paused) {
      step();
      return;
    }
    // Playing: pause through Vidstack (paused behind its back, it kept showing playing),
    // and step once it has paused (a seek in between left it showing playing too).
    video.addEventListener("pause", step, { once: true });
    remote.pause();
  };

/** A key that moves `seconds` (J / L, Shift+arrows). */
const seekBy =
  (seconds: number) =>
  ({ event, player }: { event: KeyboardEvent; player: MediaPlayerInstance }) => {
    if (!onTimeline(event)) seekVideo(player, videoTime(player) + seconds);
  };

/** Keyboard on the player: Vidstack's shortcuts, except seeking, which is ours (on the
 * video element, see `seekVideo`): the arrows jump between markers, Shift+arrows and J / L
 * move 5 s, and , / . step one frame. */
function keyShortcuts(markers: Marker[] | undefined): MediaKeyShortcuts {
  const jump =
    (direction: -1 | 1) =>
    ({ event, player }: { event: KeyboardEvent; player: MediaPlayerInstance }) => {
      if (!onTimeline(event)) jumpToMarker(player, markers, direction, KEY_SEEK_S);
    };
  return {
    ...MEDIA_KEY_SHORTCUTS,
    togglePictureInPicture: null,
    toggleCaptions: null,
    seekBackward: null,
    seekForward: null,
    back: { keys: "j J Shift+ArrowLeft", onKeyDown: seekBy(-KEY_SEEK_S) },
    forward: { keys: "l L Shift+ArrowRight", onKeyDown: seekBy(KEY_SEEK_S) },
    previousMarker: { keys: "ArrowLeft", onKeyDown: jump(-1) },
    nextMarker: { keys: "ArrowRight", onKeyDown: jump(1) },
    previousFrame: { keys: ",", onKeyDown: stepFrame(-1) },
    nextFrame: { keys: ".", onKeyDown: stepFrame(1) },
  };
}

/** Measures how long a frame lasts in the playing video, into `seconds` (cameras' frame
 * rates vary and are not in the file's metadata here), into `frameSeconds`. Inside a
 * `MediaPlayer`. */
function FrameClock() {
  const player = useMediaPlayer();
  const canPlay = useMediaState("canPlay");
  const src = useMediaState("source").src;
  useEffect(() => {
    const video = player?.el?.querySelector("video");
    // Measured again for each video, once it can play.
    if (!player || !canPlay || !src || !video || !("requestVideoFrameCallback" in video)) return;
    frameSeconds.delete(player);
    let last: number | null = null;
    const gaps: number[] = [];
    let id = 0;
    const onFrame = (_: number, meta: VideoFrameCallbackMetadata) => {
      if (!video.paused && last !== null) {
        const gap = meta.mediaTime - last;
        if (gap > 0.005 && gap < 1) {
          gaps.push(gap);
          if (gaps.length > 30) gaps.shift();
          // The median: one late frame does not move it.
          const median = gaps.toSorted((a, b) => a - b)[Math.floor(gaps.length / 2)];
          if (median) frameSeconds.set(player, median);
        }
      }
      last = video.paused ? null : meta.mediaTime;
      id = video.requestVideoFrameCallback(onFrame);
    };
    id = video.requestVideoFrameCallback(onFrame);
    return () => video.cancelVideoFrameCallback(id);
  }, [player, canPlay, src]);
  return null;
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
  const jump = (direction: -1 | 1) => player && jumpToMarker(player, markers, direction, SEEK_S);
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
  motion?: { on: boolean; toggle: () => void; frames: MotionFrame[] };
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
        <ScrubPicture
          src={thumbnails}
          time={(scrub / 100) * duration}
          motion={motion?.on ? motion.frames : undefined}
        />
      )}
      {waiting && <Spinner className="player-spinner" size="medium" />}
      {!started && paused && !waiting && scrub === null && (
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
function ScrubPicture({
  src,
  time,
  motion,
}: {
  src: string;
  time: number;
  /** Shown on the picture too, like during playback. */
  motion?: MotionFrame[];
}) {
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
          draggable={false}
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
        {motion && <MotionBoxes boxes={motionAt(motion, time * 1000)?.[1] ?? []} />}
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
  const frame = started ? motionAt(frames, ms) : undefined;
  let picture = { left: 0, top: 0, width, height };
  if (aspect && width && height) {
    const w = Math.min(width, height * aspect);
    const h = w / aspect;
    picture = { left: (width - w) / 2, top: (height - h) / 2, width: w, height: h };
  }
  return (
    <div ref={box} className="motion-overlay" aria-hidden="true">
      <div className="motion-picture" style={picture}>
        {frame && <MotionBoxes boxes={frame[1]} />}
      </div>
    </div>
  );
}

/** The frame with motion at `ms`, if any. */
function motionAt(frames: MotionFrame[], ms: number): MotionFrame | undefined {
  return frames.findLast(([at]) => at <= ms && ms < at + FRAME_MS);
}

/** Boxes around what moved, over a box the size of the picture. */
function MotionBoxes({ boxes }: { boxes: MotionFrame[1] }) {
  return boxes.map(([x, y, w, h]) => (
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
  ));
}
