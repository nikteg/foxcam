// The app's video player: Vidstack plays the video and keeps its state (keyboard
// shortcuts, gestures, hiding the controls); the controls are our own, made of Fluent UI
// parts on a dark theme, with a timeline that previews the server's thumbnail sprites
// (WebVTT) and can show markers (e.g. where things moved, footage that is missing), with
// boxes over the picture around what moved and what detectors found (components/Boxes.tsx).
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
  ScanObject20Filled,
  ScanObject20Regular,
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

import type { FootageGap, MotionFrame, PlayerDetection } from "../api";
import { useI18n } from "../i18n";
import { darkTheme } from "../theme";
import { useMediaQuery } from "../useMediaQuery";
import { DetectionBoxes, MotionBoxes, PictureBoxes } from "./Boxes";

export type PlayerHandle = MediaPlayerInstance;

/** A stretch of the video to mark on the timeline, in seconds; `kind` colours it. */
export type Marker = { from: number; to: number; kind?: "motion" | "detection" };

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
  detections,
  posterDetections,
  gaps,
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
  /** Drawn over the picture, under the controls. */
  overlay?: ReactNode;
  /** Marked on the timeline; a double tap at the sides jumps between them. */
  markers?: Marker[];
  /** Where things moved: boxes on the picture and marks on the timeline, with a button in
   * the bar to hide them (shown by default). */
  motion?: MotionFrame[];
  /** What detectors found: boxes on the picture at their moments, the matches marked on
   * the timeline, with a button in the bar to hide them (shown by default). */
  detections?: PlayerDetection[];
  /** Drawn over the poster until playback starts (the detection the poster shows). */
  posterDetections?: PlayerDetection[];
  /** Footage missing from the video: marked on the timeline (the arrows and double taps
   * jump to them too) and said on the picture while playing there. */
  gaps?: FootageGap[];
}) {
  const [showMotion, setShowMotion] = useState(true);
  const [showDetections, setShowDetections] = useState(true);
  const hasMotion = !!motion?.length;
  const motionOn = hasMotion && showMotion;
  const hasDetections = !!(detections?.length || posterDetections?.length);
  const detectionsOn = hasDetections && showDetections;
  const timelineMarkers = [
    ...(markers ?? []),
    ...(motionOn ? motionMarkers(motion) : []),
    ...(detectionsOn && detections ? detectionMarkers(detections) : []),
  ];
  const allMarkers = [...timelineMarkers, ...(gaps ?? []).map(([from, to]) => ({ from, to }))];
  // One button for both kinds of boxes: both, detections only, motion only, none, both…
  // (with one kind, on and off).
  const cycleBoxes = () => {
    const [motionNext, detectionsNext] = !hasDetections
      ? [!motionOn, false]
      : !hasMotion
        ? [false, !detectionsOn]
        : motionOn && detectionsOn
          ? [false, true]
          : detectionsOn
            ? [true, false]
            : motionOn
              ? [false, false]
              : [true, true];
    setShowMotion(motionNext);
    setShowDetections(detectionsNext);
  };
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
      {(motionOn || detectionsOn) && (
        <PlaybackBoxes
          motion={motionOn ? motion : undefined}
          detections={detectionsOn ? detections : undefined}
          poster={poster ? ((detectionsOn ? posterDetections : undefined) ?? []) : undefined}
        />
      )}
      {!!gaps?.length && <GapNotice gaps={gaps} />}
      <FrameClock />
      <PictureTaps markers={allMarkers} />
      <PlayerControls
        thumbnails={thumbnails}
        markers={timelineMarkers}
        gaps={gaps}
        motion={hasMotion ? { on: motionOn, frames: motion } : undefined}
        detections={hasDetections ? { on: detectionsOn, list: detections ?? [] } : undefined}
        cycleBoxes={cycleBoxes}
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
  const surface = useRef<HTMLDivElement>(null);
  // Vidstack shows the controls on every touchend on the player while playing, which undid
  // a tap that hid them: taps on the picture are ours alone.
  useEffect(() => {
    const el = surface.current;
    const stop = (e: TouchEvent) => e.stopPropagation();
    el?.addEventListener("touchend", stop);
    return () => el?.removeEventListener("touchend", stop);
  }, []);
  const jump = (direction: -1 | 1) => player && jumpToMarker(player, markers, direction, SEEK_S);
  const toggleControls = () => {
    if (!player) return;
    if (player.controls.showing) player.controls.hide(0);
    else {
      player.controls.show(0);
      // Hidden again after a while, as after any other touch while playing.
      if (!player.state.paused) player.controls.hide();
    }
  };
  return (
    // Pointer events only; the keyboard works on the player (Vidstack's shortcuts).
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      ref={surface}
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
        else if (!second) toggleControls();
      }}
    />
  );
}

function PlayerControls({
  thumbnails,
  markers,
  gaps,
  motion,
  detections,
  cycleBoxes,
}: {
  thumbnails?: string;
  markers?: Marker[];
  gaps?: FootageGap[];
  /** The motion, when the video has some to show, and whether its boxes are on. */
  motion?: { on: boolean; frames: MotionFrame[] };
  /** The detections, when the video has some to show, and whether their boxes are on. */
  detections?: { on: boolean; list: PlayerDetection[] };
  /** The boxes button: to the next of both, detections, motion, none. */
  cycleBoxes: () => void;
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
          detections={detections?.on ? detections.list : undefined}
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
              {duration > 0 && !!(markers?.length || gaps?.length) && (
                <span className="player-markers" aria-hidden="true">
                  {markers?.map((m) => (
                    <span
                      key={`${m.kind}-${m.from}`}
                      className={`player-marker ${m.kind ?? ""}`}
                      style={{
                        left: `${(m.from / duration) * 100}%`,
                        width: `${(Math.max(m.to - m.from, 0) / duration) * 100}%`,
                      }}
                    />
                  ))}
                  {gaps?.map(([from, to]) => (
                    <span
                      key={`gap-${from}`}
                      className={to > from ? "player-gap" : "player-gap player-gap-moment"}
                      style={{
                        left: `${(from / duration) * 100}%`,
                        width: `${((to - from) / duration) * 100}%`,
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
            {(detections || motion) && (
              <BoxesButton detections={detections?.on} motion={motion?.on} onClick={cycleBoxes} />
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
  detections,
}: {
  src: string;
  time: number;
  /** Shown on the picture too, like during playback. */
  motion?: MotionFrame[];
  detections?: PlayerDetection[];
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
        {detections && <DetectionBoxes detections={detectionsAt(detections, time * 1000)} />}
      </div>
    </div>
  );
}

/** The boxes over the picture, one button: each press goes on to the next of both,
 * detections, motion and none. Its icons are the kinds the video has, filled when shown.
 * `detections` / `motion`: undefined when the video has none. */
function BoxesButton({
  detections,
  motion,
  onClick,
}: {
  detections?: boolean;
  motion?: boolean;
  onClick: () => void;
}) {
  const { t } = useI18n();
  const label = t(
    detections && motion
      ? "player.boxesBoth"
      : detections
        ? "player.boxesDetections"
        : motion
          ? "player.boxesMotion"
          : "player.boxesOff",
  );
  return (
    <Button
      appearance="transparent"
      className="player-boxes-button"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {detections !== undefined &&
        (detections ? <ScanObject20Filled className="player-on" /> : <ScanObject20Regular />)}
      {motion !== undefined && (motion ? <Run20Filled className="player-on" /> : <Run20Regular />)}
    </Button>
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

/** How long a moment where footage is missing stays said on the picture (s). */
const GAP_NOTICE_S = 2;

/** "Footage missing" on the picture while playing where it is (inside a `VideoPlayer`). */
function GapNotice({ gaps }: { gaps: FootageGap[] }) {
  const { t } = useI18n();
  const time = useMediaState("currentTime");
  const started = useMediaState("started");
  const gap = started
    ? gaps.find(([from, to]) => time >= from - 0.1 && time < Math.max(to, from + GAP_NOTICE_S))
    : undefined;
  if (!gap) return null;
  return (
    <output className="player-gap-notice">
      {gap[2] === null
        ? t("player.footageMissingUnknown")
        : t("player.footageMissing", { seconds: Math.max(Math.round(gap[2]), 1) })}
    </output>
  );
}

/** Moments closer than this (ms) are one stretch on the timeline. */
const MARKER_GAP_MS = 1500;

/** Moments (ms) as timeline markers, each lasting `lengthMs`; close ones are one. */
function stretches(moments: number[], lengthMs: number, kind: Marker["kind"]): Marker[] {
  const spans: Marker[] = [];
  for (const at of moments) {
    const last = spans.at(-1);
    if (last && at / 1000 - last.to <= MARKER_GAP_MS / 1000) last.to = (at + lengthMs) / 1000;
    else spans.push({ from: at / 1000, to: (at + lengthMs) / 1000, kind });
  }
  return spans;
}

/** Where things moved, as timeline markers. */
export function motionMarkers(frames: MotionFrame[]): Marker[] {
  return stretches(
    frames.map(([at]) => at),
    FRAME_MS,
    "motion",
  );
}

/** Where detections that count were, as timeline markers. */
function detectionMarkers(detections: PlayerDetection[]): Marker[] {
  const moments = detections.filter((d) => d.match).map((d) => d.at_ms);
  return stretches(
    [...new Set(moments)].toSorted((a, b) => a - b),
    FRAME_MS,
    "detection",
  );
}

/** How long one frame's motion stays on screen (frames are 0.5 s apart at 2 fps). */
const FRAME_MS = 600;
/** A detection stays on screen until its detector's next moment, if that comes within
 * this (ms); else for `FRAME_MS`. Detectors look at 2 frames a second or fewer. */
const DETECTION_HOLD_MS = 1500;

/** The boxes over the playback, inside a `VideoPlayer`: what moved and what detectors
 * found at this moment. `poster`: the video has one, which covers it until playback
 * starts; meanwhile these detections (the poster's) are drawn instead. */
function PlaybackBoxes({
  motion,
  detections,
  poster,
}: {
  motion?: MotionFrame[];
  detections?: PlayerDetection[];
  poster?: PlayerDetection[];
}) {
  const time = useMediaState("currentTime");
  const canPlay = useMediaState("canPlay");
  // Until playback starts a poster covers the video; without one the video shows.
  const started = useMediaState("started");
  const live = started || !poster;
  const box = useRef<HTMLDivElement>(null);
  // The video's own aspect ratio: the picture is letterboxed inside the player.
  const [aspect, setAspect] = useState<number | null>(null);
  useEffect(() => {
    const video = box.current?.closest(".video-player")?.querySelector("video");
    if (video?.videoWidth) setAspect(video.videoWidth / video.videoHeight);
  }, [canPlay]);
  const ms = time * 1000;
  const frame = live && motion ? motionAt(motion, ms) : undefined;
  const found = live ? detectionsAt(detections ?? [], ms) : poster;
  return (
    <div ref={box} className="player-boxes">
      {aspect && (
        <PictureBoxes aspect={aspect}>
          {frame && <MotionBoxes boxes={frame[1]} />}
          <DetectionBoxes detections={found} />
        </PictureBoxes>
      )}
    </div>
  );
}

/** The frame with motion at `ms`, if any. */
function motionAt(frames: MotionFrame[], ms: number): MotionFrame | undefined {
  return frames.findLast(([at]) => at <= ms && ms < at + FRAME_MS);
}

/** Each detector's detections at its latest moment at or before `ms`, while on screen. */
function detectionsAt(detections: PlayerDetection[], ms: number): PlayerDetection[] {
  const latest = new Map<string | null, number>();
  for (const d of detections) {
    if (d.at_ms <= ms && d.at_ms > (latest.get(d.detector_id) ?? -Infinity))
      latest.set(d.detector_id, d.at_ms);
  }
  return detections.filter((d) => {
    if (latest.get(d.detector_id) !== d.at_ms) return false;
    const next = detections.find((n) => n.detector_id === d.detector_id && n.at_ms > d.at_ms);
    const until =
      next && next.at_ms - d.at_ms <= DETECTION_HOLD_MS ? next.at_ms : d.at_ms + FRAME_MS;
    return ms < until;
  });
}
