import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import { PageHeader, QueryState } from "../components/ui";
import { percent } from "../format";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";

export function Events() {
  const [{ camera }, setFilter] = useFilters(["camera"] as const);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const events = useQuery({
    queryKey: ["events", camera],
    queryFn: () => api.events({ camera, limit: 200 }),
    refetchInterval: 15_000,
  });
  const cfg = settings.data;
  const { t, rich } = useI18n();

  return (
    <>
      <PageHeader title={t("nav.events")}>
        {cfg &&
          rich("events.intro", {
            labels: <b>{cfg.detection.target_labels.join(", ")}</b>,
            confidence: percent(cfg.detection.min_confidence),
            before: cfg.events.pre_roll_seconds,
            after: cfg.events.post_roll_seconds,
          })}
      </PageHeader>
      <div className="filters">
        <CameraSelect value={camera} onChange={(v) => setFilter("camera", v)} />
      </div>
      <QueryState error={events.error} loading={events.isPending} />
      {events.data && <EventsGrid events={events.data} />}
    </>
  );
}
