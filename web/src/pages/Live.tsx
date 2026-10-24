import { Caption1, Card, CardHeader, Link, Switch, Text } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, browserUrl } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { Dot, Empty, PageHeader, QueryState } from "../components/ui";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";

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

export function Live() {
  const [{ camera }, setFilter] = useFilters(["camera"] as const);
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: api.cameras,
    refetchInterval: 15_000,
  });
  const selected = (cameras.data ?? []).filter((c) => !camera || c.id === camera);
  const { t } = useI18n();
  const [lowData, setLowData] = useLowData();
  const lowAvailable = (cameras.data ?? []).some((c) => c.webrtc_low_url);

  return (
    <>
      <PageHeader title={t("nav.live")} />
      <div className="filters">
        <CameraSelect value={camera} onChange={(v) => setFilter("camera", v)} />
        {lowAvailable && (
          <Switch
            label={t("live.lowData")}
            title={t("live.lowDataHint")}
            checked={lowData}
            onChange={(_, d) => setLowData(d.checked)}
          />
        )}
      </div>
      <QueryState error={cameras.error} loading={cameras.isPending} />
      {cameras.data?.length === 0 && <Empty>{t("live.noCameras")}</Empty>}
      <div className={`live-grid ${selected.length === 1 ? "single" : ""}`}>
        {selected.map((c) => {
          const low = lowData && c.webrtc_low_url !== null;
          const webrtc = low ? c.webrtc_low_url! : c.webrtc_url;
          const hls = low ? (c.hls_low_url ?? c.hls_url) : c.hls_url;
          return (
            <Card key={c.id}>
              <CardHeader
                header={
                  <span className="row">
                    <Dot ok={c.online} />
                    <Text weight="semibold">{c.name}</Text>
                  </span>
                }
                action={
                  <Caption1 className="muted">
                    {low ? t("live.lowDataTag") : "WebRTC"} –{" "}
                    <Link href={browserUrl(hls)} target="_blank" rel="noreferrer">
                      {t("live.hlsFallback")}
                    </Link>
                  </Caption1>
                }
              />
              {c.online ? (
                // MediaMTX's built-in WebRTC player. It is served from another origin (port), so
                // allow-same-origin does not grant it access to this app.
                <iframe
                  className="live-frame"
                  title={t("live.frameTitle", { name: c.name })}
                  key={webrtc}
                  src={`${browserUrl(webrtc)}?controls=true&muted=true&autoplay=true`}
                  // oxlint-disable-next-line react/iframe-missing-sandbox -- cross-origin player
                  sandbox="allow-scripts allow-same-origin"
                  allow="autoplay; fullscreen"
                  allowFullScreen
                />
              ) : (
                <div className="live-frame offline">{t("live.offline")}</div>
              )}
            </Card>
          );
        })}
      </div>
    </>
  );
}
