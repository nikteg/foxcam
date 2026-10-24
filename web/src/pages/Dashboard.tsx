import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  CardPreview,
  MessageBar,
  MessageBarBody,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import {
  Alert20Regular,
  CheckmarkCircle20Regular,
  Database20Regular,
  Edit20Regular,
  Folder20Regular,
  HardDrive20Regular,
  Hourglass20Regular,
  Play20Filled,
  Video20Regular,
} from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";

import { api, snapshotUrl, type Camera, type CompactionStatus } from "../api";
import { ArchivingState } from "../components/ArchivingSection";
import { useCameraName } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import {
  Dot,
  Empty,
  Light,
  PageHeader,
  QueryState,
  RouterLink,
  Stat,
  Tag,
  Time,
  useRouterHref,
  useSourceLabel,
  useStateLabel,
} from "../components/ui";
import { useI18n } from "../i18n";
import { fileSize, localTime } from "../format";

export function Dashboard() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 10_000 });
  const events = useQuery({
    queryKey: ["events", "latest"],
    queryFn: () => api.events({ limit: 6 }),
    refetchInterval: 15_000,
  });
  const s = status.data;
  const { t, rich } = useI18n();
  const stateLabel = useStateLabel();
  const routerHref = useRouterHref();

  return (
    <>
      <PageHeader title={t("nav.status")} />
      <QueryState error={status.error} loading={status.isPending} />
      {s && !s.mediamtx_reachable && (
        <MessageBar intent="warning" className="block">
          <MessageBarBody>{t("dashboard.mediamtxDown")}</MessageBarBody>
        </MessageBar>
      )}
      {s && (
        <section className="stats">
          <Stat
            icon={<Video20Regular />}
            label={t("dashboard.camerasOnline")}
            value={`${s.cameras.filter((c) => c.online).length} / ${s.cameras.length}`}
          />
          <Stat icon={<Alert20Regular />} label={t("dashboard.events24h")} value={s.events_24h} />
          <Stat
            icon={<Hourglass20Regular />}
            label={t("dashboard.queueWaiting")}
            value={s.queue.counts.pending + s.queue.counts.failed}
          />
          <Stat
            icon={<CheckmarkCircle20Regular />}
            label={t("dashboard.processedLastHour")}
            value={s.queue.done_last_hour}
          />
          <Stat
            icon={<Database20Regular />}
            label={t("dashboard.recordingsStored")}
            value={fileSize(s.recordings_bytes)}
          />
          <Stat
            icon={<HardDrive20Regular />}
            label={t("dashboard.diskFree")}
            value={fileSize(s.disk_free_bytes)}
          />
        </section>
      )}

      <Subtitle2 as="h2" className="section">
        {t("nav.cameras")}
        <Button as="a" size="small" className="section-action" {...routerHref("/cameras")}>
          {t("dashboard.manage")}
        </Button>
      </Subtitle2>
      <div className="cards">
        {s?.cameras.map((c) => (
          <CameraCard
            key={c.id}
            camera={c}
            refreshKey={Math.floor(status.dataUpdatedAt / 10_000)}
          />
        ))}
      </div>
      {s && s.cameras.length === 0 && (
        <Empty>
          {rich("dashboard.noCameras", {
            link: <RouterLink to="/cameras">{t("dashboard.addCameraLink")}</RouterLink>,
          })}
        </Empty>
      )}

      <Subtitle2 as="h2" className="section">
        {t("dashboard.latestEvents")}
        <Button as="a" size="small" className="section-action" {...routerHref("/events")}>
          {t("dashboard.all")}
        </Button>
      </Subtitle2>
      <EventsGrid events={events.data ?? []} />

      {s && (
        <>
          <Subtitle2 as="h2" className="section">
            {t("dashboard.segments")}
          </Subtitle2>
          <div className="row wrap tags">
            {Object.entries(s.segments).map(([state, n]) => (
              <RouterLink
                key={state}
                to={`/recordings?status=${state}`}
                className="tag-link"
                title={t("dashboard.showRecordings")}
              >
                <Tag kind={state}>
                  {stateLabel(state)}: {n}
                </Tag>
              </RouterLink>
            ))}
          </div>
        </>
      )}

      {s?.compaction && <ArchivingSummary c={s.compaction} />}
    </>
  );
}

/** The archiving process in one line (admins); details under Jobs → Archiving. */
function ArchivingSummary({ c }: { c: CompactionStatus }) {
  const { t } = useI18n();
  const cameraName = useCameraName();
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("compaction.title")} <ArchivingState c={c} />
      </Subtitle2>
      <p className="muted">
        {c.current
          ? t(c.current.reencode ? "compaction.reencoding" : "compaction.joining", {
              what: `${cameraName(c.current.camera_id)} ${localTime(c.current.start_at)}`,
              n: c.current.segments,
            })
          : c.waiting_periods
            ? t("archiving.summaryWaiting", { n: c.waiting_periods })
            : t("archiving.summaryUpToDate")}{" "}
        <RouterLink to="/jobs/archiving">{t("archiving.details")}</RouterLink>
      </p>
    </>
  );
}

function CameraCard({ camera: c, refreshKey }: { camera: Camera; refreshKey: number }) {
  const routerHref = useRouterHref();
  const { t } = useI18n();
  const sourceLabel = useSourceLabel();
  return (
    <Card>
      <CardHeader
        header={
          <span className="row">
            <Dot ok={c.online} />
            <Text weight="semibold">{c.name}</Text>
            <Caption1 className="muted">{sourceLabel(c.source)}</Caption1>
          </span>
        }
        action={
          // Fixed-height row; the light tag keeps its place (hidden) until the first
          // segment has been tagged, so the header never changes height.
          <span className="card-status">
            {c.private && <Tag kind="private">{t("state.private")}</Tag>}
            <span className={c.last_is_night === null ? "placeholder" : undefined}>
              <Tag>
                <Light night={c.last_is_night ?? false} long small />
              </Tag>
            </span>
          </span>
        }
      />
      <CardPreview>
        <a
          className="thumb-button"
          aria-label={t("dashboard.openLive", { name: c.name })}
          {...routerHref(`/live?camera=${c.id}`)}
        >
          {/* refreshKey re-requests the snapshot together with the status poll. */}
          <img
            className="thumb"
            loading="lazy"
            src={`${snapshotUrl(c.id)}?t=${refreshKey}`}
            alt={t("dashboard.snapshotAlt", { name: c.name })}
            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
          />
          <span className="play-overlay" aria-hidden="true">
            <Play20Filled />
          </span>
        </a>
      </CardPreview>
      <dl className="facts">
        <dt>{t("camera.segmentLength")}</dt>
        <dd>{c.segment_seconds}s</dd>
        <dt>{t("camera.lastSegment")}</dt>
        <dd>
          <Time iso={c.last_segment_at} />
        </dd>
        <dt>{t("camera.censorAreas")}</dt>
        <dd>{c.rects.length}</dd>
        <dt>{t("camera.viewers")}</dt>
        <dd>{c.readers}</dd>
      </dl>
      <CardFooter className="wrap">
        <Button as="a" {...routerHref(`/live?camera=${c.id}`)} icon={<Video20Regular />}>
          {t("nav.live")}
        </Button>
        <Button as="a" {...routerHref(`/recordings?camera=${c.id}`)} icon={<Folder20Regular />}>
          {t("nav.recordings")}
        </Button>
        <Button as="a" {...routerHref(`/cameras/${c.id}/mask`)} icon={<Edit20Regular />}>
          {t("camera.censorAreas")}
        </Button>
      </CardFooter>
    </Card>
  );
}
