// Jobs → Archiving: the process that joins finished periods into one archive file per
// camera (`foxcam compact`, pipeline/compaction.py). The files themselves are
// under Recordings → Archives.
import { MessageBar, MessageBarBody, Subtitle2 } from "@fluentui/react-components";
import {
  Archive20Regular,
  ArrowSync20Regular,
  Clock20Regular,
  Hourglass20Regular,
} from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { api } from "../api";
import { duration, fileSize, localTime } from "../format";
import { useI18n, type I18n } from "../i18n";
import { ArchivePanel } from "../pages/HourlyFiles";
import { useFilters } from "../useFilters";
import { byNumber, byString, DataTable, type Column } from "./DataTable";
import { CameraTag, useCameraName } from "./CameraSelect";
import { Empty, InfoBanner, QueryState, Stat, Tag, Time } from "./ui";

interface PeriodRow {
  key: string;
  camera: string;
  start: string;
  segments: number;
  state: "archiving" | "waiting" | "archived";
  archiveId?: number;
  duration?: number;
  size?: number;
}

const STATE_ORDER = { archiving: 0, waiting: 1, archived: 2 };
const STATE_TAG = { archiving: "leased", waiting: "pending", archived: "done" };

const periodColumns = (t: I18n["t"], cameraName: (id: string) => string): Column<PeriodRow>[] => [
  {
    id: "status",
    header: t("col.status"),
    width: 120,
    cell: (r) => <Tag kind={STATE_TAG[r.state]}>{t(`archiving.state.${r.state}`)}</Tag>,
    // Now, then waiting (oldest first, the order they are done in), then the newest files.
    compare: (a, b) =>
      STATE_ORDER[a.state] - STATE_ORDER[b.state] ||
      (a.state === "archived" ? b.start.localeCompare(a.start) : a.start.localeCompare(b.start)),
  },
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (r) => <CameraTag id={r.camera} />,
    compare: byString((r) => cameraName(r.camera)),
  },
  {
    id: "start",
    header: t("archiving.period"),
    width: 200,
    cell: (r) => <Time iso={r.start} />,
    compare: byString((r) => r.start),
  },
  {
    id: "segments",
    header: t("hourly.segments"),
    width: 90,
    cell: (r) => r.segments,
    compare: byNumber((r) => r.segments),
  },
  {
    id: "length",
    header: t("col.length"),
    width: 90,
    cell: (r) => (r.duration ? duration(r.duration) : "–"),
    compare: byNumber((r) => r.duration),
  },
  {
    id: "size",
    header: t("col.size"),
    width: 100,
    cell: (r) => (r.size ? fileSize(r.size) : "–"),
    compare: byNumber((r) => r.size),
  },
];

export function ArchivingSection() {
  const { t, rich } = useI18n();
  const cameraName = useCameraName();
  const navigate = useNavigate();
  const [{ file }, setFilter] = useFilters(["file"] as const);
  const status = useQuery({
    queryKey: ["archiving"],
    queryFn: api.archiving,
    refetchInterval: 10_000,
  });
  const recent = useQuery({
    queryKey: ["archives", "recent"],
    queryFn: () => api.archives({ limit: 20 }),
    refetchInterval: 30_000,
  });
  const c = status.data;
  if (!c) return <QueryState error={status.error} loading={status.isPending} />;
  // One row per period: being archived now, waiting, or archived (the latest files).
  const rows: PeriodRow[] = [
    ...c.waiting_list.map((w): PeriodRow => {
      const now =
        c.current?.camera_id === w.camera_id &&
        new Date(c.current.start_at).getTime() >= new Date(w.start_at).getTime() &&
        new Date(c.current.start_at).getTime() <
          new Date(w.start_at).getTime() + c.period_minutes * 60_000;
      return {
        key: `w/${w.camera_id}/${w.start_at}`,
        camera: w.camera_id,
        start: w.start_at,
        segments: w.segments,
        state: now ? "archiving" : "waiting",
      };
    }),
    ...(recent.data ?? []).map((a): PeriodRow => ({
      key: `a/${a.id}`,
      camera: a.camera_id,
      start: a.start_at,
      segments: a.segments,
      state: "archived",
      archiveId: a.id,
      duration: a.duration_s,
      size: a.size_bytes,
    })),
  ];
  const selected = recent.data?.find((a) => a.id === Number(file));
  const last = c.last_built;
  const errorIsNewer = c.last_error && (!last || (c.last_error_at ?? "") > last.at);

  return (
    <>
      <InfoBanner id="archiving">{t("archiving.intro", { minutes: c.period_minutes })}</InfoBanner>
      {c.enabled && !c.running && (
        <MessageBar intent="warning" className="block">
          <MessageBarBody>
            {rich("compaction.notRunning", { command: <code>foxcam compact</code> })}
          </MessageBarBody>
        </MessageBar>
      )}
      {errorIsNewer && (
        <MessageBar intent="error" className="block">
          <MessageBarBody>{c.last_error}</MessageBarBody>
        </MessageBar>
      )}
      <section className="stats">
        <Stat
          icon={<Clock20Regular />}
          label={t("archiving.process")}
          value={
            !c.enabled
              ? t("compaction.off")
              : c.running
                ? t("compaction.running")
                : t("compaction.stopped")
          }
          hint={
            c.heartbeat_at && (
              <>
                {t("archiving.heard")} <Time iso={c.heartbeat_at} relative />
                {c.host && ` – ${c.host}`}
              </>
            )
          }
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("compaction.now")}
          value={c.current ? cameraName(c.current.camera_id) : t("compaction.idle")}
          hint={
            c.current &&
            t(c.current.reencode ? "compaction.reencoding" : "compaction.joining", {
              what: localTime(c.current.start_at),
              n: c.current.segments,
            })
          }
        />
        <Stat
          icon={<Hourglass20Regular />}
          label={t("compaction.waiting")}
          value={c.waiting_periods}
          hint={t("archiving.segmentsHint", { n: c.waiting_segments })}
        />
        <Stat
          icon={<Archive20Regular />}
          label={t("archiving.files")}
          value={c.archives}
          hint={t("archiving.segmentsHint", { n: c.joined_segments })}
        />
      </section>

      <Subtitle2 as="h2" className="section">
        {t("archiving.periodsTitle")}
      </Subtitle2>
      <div className="master-detail">
        <div className="master">
          {rows.length ? (
            <DataTable
              ariaLabel={t("archiving.periodsTitle")}
              items={rows}
              columns={periodColumns(t, cameraName)}
              getRowId={(r) => r.key}
              defaultSort={{ column: "status", direction: "ascending" }}
              selectedId={selected && `a/${selected.id}`}
              onRowClick={(r) => r.archiveId && setFilter("file", String(r.archiveId))}
            />
          ) : (
            <Empty>{t("hourly.none")}</Empty>
          )}
        </div>
        <ArchivePanel
          archive={selected}
          onClose={() => setFilter("file", "")}
          onShowSegments={(a) =>
            void navigate(
              `/recordings/segments?camera=${a.camera_id}&day=${a.start_at.slice(0, 10)}`,
            )
          }
        />
      </div>
    </>
  );
}
