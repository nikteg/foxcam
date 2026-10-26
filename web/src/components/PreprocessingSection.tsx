// Jobs → Preparation: recordings the server prepares before they get detection jobs (a
// censored copy for the workers, motion, thumbnails; pipeline/preprocess.py). A backlog
// here is invisible in the job queue: those recordings have no jobs yet.
import { Subtitle2 } from "@fluentui/react-components";
import {
  AlertUrgent20Regular,
  ArrowSync20Regular,
  ArrowTrending20Regular,
  Gauge20Regular,
  Hourglass20Regular,
  Timer20Regular,
} from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type PreprocessingSegment } from "../api";
import { decimal, duration } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { CameraTag, useCameraName } from "./CameraSelect";
import { byString, DataTable, type Column } from "./DataTable";
import { SegmentPanel } from "./SegmentDetail";
import { Empty, InfoBanner, QueryState, Stat, Tag, Time } from "./ui";
import { TableSkeleton } from "./Skeletons";

const columns = (
  t: I18n["t"],
  cameraName: (id: string) => string,
): Column<PreprocessingSegment>[] => [
  {
    id: "status",
    header: t("col.status"),
    width: 120,
    cell: (g) =>
      g.started_at ? (
        <Tag kind="leased">{t("prep.statePreparing")}</Tag>
      ) : (
        <Tag kind="pending">{t("prep.stateWaiting")}</Tag>
      ),
  },
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (g) => <CameraTag id={g.camera_id} />,
    compare: byString((g) => cameraName(g.camera_id)),
  },
  {
    id: "start",
    header: t("col.segmentStart"),
    width: 200,
    cell: (g) => <Time iso={g.start_at} />,
    compare: byString((g) => g.start_at),
  },
  {
    id: "started",
    header: t("prep.started"),
    width: 130,
    cell: (g) => <Time iso={g.started_at} relative />,
  },
];

export function PreprocessingSection() {
  const { t } = useI18n();
  const cameraName = useCameraName();
  const [{ segment }, setFilter] = useFilters(["segment"] as const);
  const status = useQuery({
    queryKey: ["preprocessing"],
    queryFn: () => api.preprocessing(),
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  });
  const p = status.data;
  if (!p) {
    return (
      <QueryState error={status.error} loading={status.isPending} skeleton={<TableSkeleton />} />
    );
  }
  const segmentId = Number(segment) || null;

  return (
    <>
      <InfoBanner id="preprocessing">{t("prep.intro")}</InfoBanner>
      <section className="stats">
        <Stat icon={<Hourglass20Regular />} label={t("prep.waiting")} value={p.waiting} />
        <Stat
          icon={<Timer20Regular />}
          label={t("queue.oldestWaiting")}
          value={duration(p.oldest_waiting_age_s)}
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("prep.preparing")}
          value={`${p.preparing} / ${p.concurrency}`}
          hint={t("prep.concurrencyHint")}
        />
        <Stat
          icon={<ArrowTrending20Regular />}
          label={t("queue.doneLastHour")}
          value={p.done_last_hour}
        />
        <Stat
          icon={<Gauge20Regular />}
          label={t("prep.avg")}
          value={p.avg_seconds_last_hour != null ? `${decimal(p.avg_seconds_last_hour, 1)} s` : "–"}
        />
        <Stat
          icon={<AlertUrgent20Regular />}
          label={t("queue.failedCount")}
          value={p.failed}
          hint={
            p.failed_last_hour ? t("prep.failedLastHour", { n: p.failed_last_hour }) : undefined
          }
        />
      </section>

      <Subtitle2 as="h2" className="section">
        {t("prep.next")}
      </Subtitle2>
      {p.segments.length ? (
        <DataTable
          ariaLabel={t("prep.next")}
          items={p.segments}
          columns={columns(t, cameraName)}
          getRowId={(g) => g.id}
          rowClassName={(g) => (g.id === segmentId ? "selected" : undefined)}
          onRowClick={(g) => setFilter("segment", String(g.id))}
        />
      ) : (
        <Empty>{t("prep.none")}</Empty>
      )}
      <SegmentPanel segmentId={segmentId} onClose={() => setFilter("segment", "")} />
    </>
  );
}
