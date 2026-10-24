import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import { Select } from "@fluentui/react-components";

import { ProfileSelect, useProfiles, useSeveralDetectors } from "../components/ProfileSelect";
import { InfoBanner, PageTitle, QueryState } from "../components/ui";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";

export function Events() {
  const [{ camera, profile, agreement }, setFilter] = useFilters([
    "camera",
    "profile",
    "agreement",
  ] as const);
  const severalDetectors = useSeveralDetectors();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const profiles = useProfiles();
  const events = useQuery({
    queryKey: ["events", camera, profile, agreement],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.events({ camera, profile, min_agreement: Number(agreement) || undefined, limit: 200 }),
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
      <PageTitle title={t("nav.events")} />
      {cfg && (
        <InfoBanner id="events">
          {rich("events.intro", {
            labels: <b>{targets.join(", ")}</b>,
            before: cfg.events.pre_roll_seconds,
            after: cfg.events.post_roll_seconds,
          })}
        </InfoBanner>
      )}
      <div className="filters">
        <CameraSelect value={camera} onChange={(v) => setFilter("camera", v)} />
        <ProfileSelect value={profile} onChange={(v) => setFilter("profile", v)} />
        {(severalDetectors || agreement) && (
          <Select
            value={agreement}
            aria-label={t("events.seenBy")}
            onChange={(_, d) => setFilter("agreement", d.value)}
          >
            <option value="">{t("events.seenByAny")}</option>
            {[2, 3].map((n) => (
              <option key={n} value={String(n)}>
                {t("events.seenByAtLeast", { n })}
              </option>
            ))}
          </Select>
        )}
      </div>
      <QueryState error={events.error} loading={events.isPending} />
      {events.data && <EventsGrid events={events.data} />}
    </>
  );
}
