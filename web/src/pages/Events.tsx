import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import {
  FeedbackSelect,
  asFeedback,
  asLight,
  LightSelect,
  PERIOD_KEYS,
  PeriodFilter,
  periodBounds,
} from "../components/Filters";
import { Input, MessageBar, MessageBarBody, Select } from "@fluentui/react-components";

import { ModelsSection } from "../components/ModelsSection";
import { NoCameras, useNoCameras } from "../components/NoCameras";
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
  const noCameras = useNoCameras();
  const tab = useSubPage(
    "/events",
    isAdmin
      ? (["events", "detections", "models", "motion"] as const)
      : (["events", "detections"] as const),
  );
  if (noCameras) {
    return (
      <>
        <PageTitle title={t("nav.events")} />
        <NoCameras />
      </>
    );
  }
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
  const [f, setFilter] = useFilters([
    "camera",
    "profile",
    "label",
    "agreement",
    "light",
    "feedback",
    "favorite",
    ...PERIOD_KEYS,
  ] as const);
  const { camera, profile, agreement, favorite } = f;
  const severalDetectors = useSeveralDetectors();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const profiles = useProfiles();
  const events = useQuery({
    queryKey: ["events", f],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.events({
        camera,
        profile,
        label: f.label,
        min_agreement: Number(agreement) || undefined,
        favorite: favorite === "true",
        light: asLight(f.light),
        feedback: asFeedback(f.feedback),
        ...periodBounds(f),
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
  const isAdmin = useIsAdmin();
  const selection = useSelection(
    events.data,
    (e) => e.id,
    (e) => canLabel(e.camera_id),
  );

  return (
    <>
      {profiles.data && targets.length === 0 && !profile && (
        <MessageBar className="block">
          <MessageBarBody>
            {isAdmin
              ? rich("events.noProfiles", {
                  link: (
                    <RouterLink to="/cameras/profiles">{t("events.noProfilesLink")}</RouterLink>
                  ),
                })
              : t("events.noProfilesUser")}
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
        <PeriodFilter value={f} onChange={setFilter} />
        <Input
          placeholder={t("detections.labelPlaceholder")}
          aria-label={t("col.label")}
          defaultValue={f.label}
          onKeyDown={(e) => e.key === "Enter" && setFilter("label", e.currentTarget.value.trim())}
          onBlur={(e) => setFilter("label", e.currentTarget.value.trim())}
        />
        <LightSelect value={f.light} onChange={(v) => setFilter("light", v)} />
        <FeedbackSelect value={f.feedback} onChange={(v) => setFilter("feedback", v)} />
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
        <BulkBar
          kind="events"
          selection={selection}
          items={events.data}
          getId={(e) => e.id}
          isFavorite={(e) => e.favorite}
        />
      )}
    </>
  );
}
