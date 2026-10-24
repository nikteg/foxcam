// Recordings player (Vidstack): hover previews on the timeline from the server's thumbnail
// sprites (WebVTT), keyboard shortcuts and the same look on every browser.
import { MediaPlayer, MediaProvider, Poster, type MediaPlayerInstance } from "@vidstack/react";
import { DefaultVideoLayout, defaultLayoutIcons } from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";
import { useRef, type RefObject } from "react";

export type PlayerHandle = MediaPlayerInstance;

export function VideoPlayer({
  src,
  title,
  poster,
  thumbnails,
  startAt,
  autoPlay = false,
  playerRef,
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
      <DefaultVideoLayout
        icons={defaultLayoutIcons}
        thumbnails={thumbnails}
        // The compact layout hides the timeline until playback starts; the drawers are
        // wide enough for the full one, so scrubbing (with previews) works right away.
        smallLayoutWhen={({ width }) => width < 400}
      />
    </MediaPlayer>
  );
}
