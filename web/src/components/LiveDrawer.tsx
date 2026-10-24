// Live view of one camera in a wide drawer (opened from a camera card's picture), with the
// low-data switch and the HLS fallback in the title row.
import { Button, Switch } from "@fluentui/react-components";
import { Open20Regular } from "@fluentui/react-icons";
import { useState } from "react";

import type { Camera } from "../api";
import { useI18n } from "../i18n";
import { Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle } from "./Drawer";
import { Tag } from "./ui";

const LOW_DATA_KEY = "media-streamer.live.lowData";

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
              <span className="live-title-controls">
                {c.webrtc_low_url !== null && (
                  <Switch
                    label={t("live.lowData")}
                    title={t("live.lowDataHint")}
                    checked={lowData}
                    onChange={(_, d) => setLowData(d.checked)}
                  />
                )}
                <Tag>{low ? t("live.lowDataTag") : "WebRTC"}</Tag>
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
              </span>
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
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
