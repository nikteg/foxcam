// Live view of one camera in a wide drawer (opened from a camera card's picture), with the
// stream's facts under it, then the low-data switch and the HLS fallback.
import { Button, Switch } from "@fluentui/react-components";
import { Open20Regular } from "@fluentui/react-icons";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { Camera, LiveStream } from "../api";
import { decimal, duration } from "../format";
import { useI18n } from "../i18n";
import {
  CloseActions,
  Dialog,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";
import { TapoNameFact } from "./CameraCard";
import { Fact, Facts, NoValue, Tag, Time } from "./ui";

const LOW_DATA_KEY = "foxcam.live.lowData";

/** Low-data mode (a transcoded ~480p stream), remembered per browser. */
function useLowData(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => localStorage.getItem(LOW_DATA_KEY) === "1");
  return [
    on,
    (next) => {
      setOn(next);
      if (next) localStorage.setItem(LOW_DATA_KEY, "1");
      else localStorage.removeItem(LOW_DATA_KEY);
    },
  ];
}

export function LiveDrawer({
  cameras,
  cameraId,
  onClose,
}: {
  cameras: Camera[];
  cameraId: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [lowData, setLowData] = useLowData();
  const c = cameras.find((x) => x.id === cameraId);
  if (!c) return null;
  const low = lowData && c.webrtc_low_url !== null;
  const webrtc = low ? c.webrtc_low_url! : c.webrtc_url;
  const hls = low ? (c.hls_low_url ?? c.hls_url) : c.hls_url;
  // The low-data stream starts when someone watches it: until it is up, there is nothing to
  // tell about it (the main stream's facts would be the wrong ones).
  const stream = low ? c.low_stream : c.stream;

  return (
    <Dialog open size="large" onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>
            <span className="live-title">
              {c.name}
              {c.online ? (
                <output className="live-badge">
                  <span className="live-badge-dot" aria-hidden="true" />
                  {t("live.badge")}
                </output>
              ) : (
                <output className="live-badge offline">{t("live.badgeOffline")}</output>
              )}
            </span>
          </DialogTitle>
          <DialogContent className="form-stack">
            {c.online ? (
              // MediaMTX's built-in WebRTC player, served through the server's
              // login-checking proxy (/api/live/...) on this origin.
              <iframe
                className="live-frame"
                title={t("live.frameTitle", { name: c.name })}
                key={webrtc}
                src={`${webrtc}?controls=true&muted=true&autoplay=true`}
                // oxlint-disable-next-line react/iframe-missing-sandbox -- cross-origin player
                sandbox="allow-scripts allow-same-origin"
                allow="autoplay; fullscreen"
                allowFullScreen
              />
            ) : (
              <div className="live-frame offline">{t("live.offline")}</div>
            )}
            <LiveFacts camera={c} stream={stream} />
            <div className="live-controls">
              {c.webrtc_low_url !== null && (
                <Switch
                  label={t("live.lowData")}
                  title={t("live.lowDataHint")}
                  checked={lowData}
                  onChange={(_, d) => setLowData(d.checked)}
                />
              )}
              <Button
                as="a"
                size="small"
                icon={<Open20Regular />}
                href={hls}
                target="_blank"
                rel="noreferrer"
                title={t("live.hlsFallbackHint")}
              >
                {t("live.hlsFallback")}
              </Button>
            </div>
          </DialogContent>
          <CloseActions />
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

/** While the stream's facts are still coming (the low-data stream starting, the bitrate
 * needing a second look), the camera list is fetched more often than the page does. */
function useFactsPolling(waiting: boolean) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => {
      for (const key of [["cameras"], ["status"]])
        void queryClient.refetchQueries({ queryKey: key, type: "active" });
    }, 3000);
    return () => clearInterval(id);
  }, [waiting, queryClient]);
}

/** The current time, again every second (a running "online for"). */
function useNow(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** 2400 -> "2.4 Mbit/s", 850 -> "850 kbit/s". */
function bitrate(kbit: number): string {
  return kbit >= 1000 ? `${decimal(kbit / 1000)} Mbit/s` : `${Math.round(kbit)} kbit/s`;
}

/** What the camera is sending right now (the stream being watched), and its recent events. */
function LiveFacts({ camera: c, stream }: { camera: Camera; stream: LiveStream | null }) {
  const { t } = useI18n();
  const now = useNow();
  useFactsPolling(c.online && stream?.bitrate_kbit == null);
  return (
    <Facts>
      <TapoNameFact camera={c} />
      {c.online && (
        <>
          <Fact label={t("live.onlineFor")}>
            {c.online_since ? (
              duration((now - new Date(c.online_since).getTime()) / 1000)
            ) : (
              <NoValue />
            )}
          </Fact>
          <Fact label={t("live.stream")}>
            <span className="chips">
              <Tag>WebRTC</Tag>
              {stream && (
                <>
                  {stream.width && stream.height && (
                    <Tag>
                      {stream.width} × {stream.height}
                    </Tag>
                  )}
                  {stream.codecs.map((codec) => (
                    <Tag key={codec}>{codec}</Tag>
                  ))}
                </>
              )}
            </span>
          </Fact>
          <Fact label={t("live.bitrate")}>
            {stream?.bitrate_kbit != null ? bitrate(stream.bitrate_kbit) : <NoValue />}
          </Fact>
        </>
      )}
      <Fact label={t("camera.viewers")}>{c.viewers}</Fact>
      <Fact label={t("live.events24h")}>{c.events_24h}</Fact>
      <Fact label={t("live.lastEvent")}>
        <Time iso={c.last_event_at} relative />
      </Fact>
    </Facts>
  );
}
