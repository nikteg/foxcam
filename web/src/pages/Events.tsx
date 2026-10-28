import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import { MessageBar, MessageBarBody, Select } from "@fluentui/react-components";

import { ModelsSection } from "../components/ModelsSection";
import { MotionSection } from "../components/MotionSection";
import { ProfileSelect, useProfiles, useSeveralDetectors } from "../components/ProfileSelect";
import {
  Checkbox,
  InfoBanner,
  PageTabs,
  PageTitle,
  QueryState,
  RouterLink,
  useSubPage,
} from "../components/ui";
import { useCanLabel, useIsAdmin } from "../auth";
import { BulkBar, useSelection } from "../components/Bulk";
import { useI18n } from "../i18n";
import { useFilters } from "../useFilters";
import { DetectionList } from "./Detections";
import { CardsSkeleton, TextSkeleton } from "../components/Skeletons";

/** Events (what happened, as clips), the detections behind them and, for admins, how the
 * models did. */
export function Events() {
  const { t } = useI18n();
  const isAdmin = useIsAdmin();
  const tab = useSubPage(
    "/events",
    isAdmin
      ? (["events", "detections", "models", "motion"] as const)
      : (["events", "detections"] as const),
  );
  return (
    <>
      <PageTitle title={t("nav.events")} />
      <PageTabs
        base="/events"
        current={tab}
        tabs={[
          { id: "events", label: t("nav.events") },
          { id: "detections", label: t("nav.detections") },
          ...(isAdmin
            ? [
                { id: "models" as const, label: t("models.tab") },
                { id: "motion" as const, label: t("motion.tab") },
              ]
            : []),
        ]}
      />
      {tab === "models" ? (
        <ModelsSection />
      ) : tab === "motion" ? (
        <MotionSection />
      ) : tab === "detections" ? (
        <DetectionList />
      ) : (
        <EventList />
      )}
    </>
  );
}

function EventList() {
  const [{ camera, profile, agreement, favorite }, setFilter] = useFilters([
    "camera",
    "profile",
    "agreement",
    "favorite",
  ] as const);
  const severalDetectors = useSeveralDetectors();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const profiles = useProfiles();
  const events = useQuery({
    queryKey: ["events", camera, profile, agreement, favorite],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.events({
        camera,
        profile,
        min_agreement: Number(agreement) || undefined,
        favorite: favorite === "true",
        limit: 200,
      }),
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
  const canLabel = useCanLabel();
  const selection = useSelection(
    events.data,
    (e) => e.id,
    (e) => canLabel(e.camera_id),
  );

  return (
    <>
      {profiles.data && targets.length === 0 && !profile && (
        <MessageBar>
          <MessageBarBody>
            {rich("events.noProfiles", {
              link: <RouterLink to="/cameras/profiles">{t("events.noProfilesLink")}</RouterLink>,
            })}
          </MessageBarBody>
        </MessageBar>
      )}
      {cfg && targets.length > 0 && (
        <InfoBanner id="events">
          {rich("events.intro", {
            labels: <b>{targets.join(", ")}</b>,
            before: cfg.events.pre_roll_seconds,
            after: cfg.events.post_roll_seconds,
          })}
        </InfoBanner>
      )}
      {/* Its wording needs the profiles and settings: its place is kept meanwhile. */}
      {(settings.isPending || profiles.isPending) && (
        <InfoBanner id="events">
          <TextSkeleton />
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
        <Checkbox
          label={t("favorite.filter")}
          checked={favorite === "true"}
          onChange={(_, d) => setFilter("favorite", d.checked === true)}
        />
      </div>
      <QueryState error={events.error} loading={events.isPending} skeleton={<CardsSkeleton />} />
      {events.data && <EventsGrid events={events.data} byDay selection={selection} />}
      {events.data && (
        <BulkBar kind="events" selection={selection} items={events.data} getId={(e) => e.id} />
      )}
    </>
  );
}
