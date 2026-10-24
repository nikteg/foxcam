import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import { ProfileSelect, useProfiles } from "../components/ProfileSelect";
import { PageHeader, QueryState } from "../components/ui";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";

export function Events() {
  const [{ camera, profile }, setFilter] = useFilters(["camera", "profile"] as const);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const profiles = useProfiles();
  const events = useQuery({
    queryKey: ["events", camera, profile],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () => api.events({ camera, profile, limit: 200 }),
    refetchInterval: 15_000,
  });
  const targets = [
    ...new Set(
      (profiles.data ?? [])
        .filter((p) => p.enabled && (!profile || p.id === profile))
        .flatMap((p) => p.targets),
    ),
  ];
  const cfg = settings.data;
  const { t, rich } = useI18n();

  return (
    <>
      <PageHeader title={t("nav.events")}>
        {cfg &&
          rich("events.intro", {
            labels: <b>{targets.join(", ")}</b>,
            before: cfg.events.pre_roll_seconds,
            after: cfg.events.post_roll_seconds,
          })}
      </PageHeader>
      <div className="filters">
        <CameraSelect value={camera} onChange={(v) => setFilter("camera", v)} />
        <ProfileSelect value={profile} onChange={(v) => setFilter("profile", v)} />
      </div>
      <QueryState error={events.error} loading={events.isPending} />
      {events.data && <EventsGrid events={events.data} />}
    </>
  );
}
