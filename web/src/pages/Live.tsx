import { Caption1, Card, CardHeader, Link, Text } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { Dot, Empty, PageHeader, QueryState } from "../components/ui";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";

export function Live() {
  const [{ camera }, setFilter] = useFilters(["camera"] as const);
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: api.cameras,
    refetchInterval: 15_000,
  });
  const selected = (cameras.data ?? []).filter((c) => !camera || c.id === camera);
  const { t } = useI18n();

  return (
    <>
      <PageHeader title={t("nav.live")} />
      <div className="filters">
        <CameraSelect value={camera} onChange={(v) => setFilter("camera", v)} />
      </div>
      <QueryState error={cameras.error} loading={cameras.isPending} />
      {cameras.data?.length === 0 && <Empty>{t("live.noCameras")}</Empty>}
      <div className={`live-grid ${selected.length === 1 ? "single" : ""}`}>
        {selected.map((c) => (
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
                  WebRTC –{" "}
                  <Link href={c.hls_url} target="_blank" rel="noreferrer">
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
                src={`${c.webrtc_url}?controls=true&muted=true&autoplay=true`}
                // oxlint-disable-next-line react/iframe-missing-sandbox -- cross-origin player
                sandbox="allow-scripts allow-same-origin"
                allow="autoplay; fullscreen"
                allowFullScreen
              />
            ) : (
              <div className="live-frame offline">{t("live.offline")}</div>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}
