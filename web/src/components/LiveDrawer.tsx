// Live view of one camera in a wide drawer (opened from a camera card's picture), with a
// camera picker to flip through the others and the low-data switch.
import { Caption1, Link, Select, Switch } from "@fluentui/react-components";
import { useState } from "react";

import type { Camera } from "../api";
import { useI18n } from "../i18n";
import { Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle } from "./Drawer";
import { Dot } from "./ui";

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
  onCamera,
  onClose,
}: {
  cameras: Camera[];
  cameraId: string;
  onCamera: (id: string) => void;
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
            <span className="row">
              <Dot ok={c.online} />
              {c.name}
            </span>
          </DialogTitle>
          <DialogContent className="form-stack">
            <div className="filters">
              {cameras.length > 1 && (
                <Select
                  value={c.id}
                  aria-label={t("common.camera")}
                  onChange={(_, d) => onCamera(d.value)}
                >
                  {cameras.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
                </Select>
              )}
              {c.webrtc_low_url !== null && (
                <Switch
                  label={t("live.lowData")}
                  title={t("live.lowDataHint")}
                  checked={lowData}
                  onChange={(_, d) => setLowData(d.checked)}
                />
              )}
              <Caption1 className="muted">
                {low ? t("live.lowDataTag") : "WebRTC"} –{" "}
                <Link href={hls} target="_blank" rel="noreferrer">
                  {t("live.hlsFallback")}
                </Link>
              </Caption1>
            </div>
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
