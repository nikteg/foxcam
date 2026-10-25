import { Select, Tooltip } from "@fluentui/react-components";
import { Archive16Regular, Pin16Filled } from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { api, type Segment } from "../api";
import { CameraSelect, useCameraName } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { SegmentPanel } from "../components/SegmentDetail";
import {
  Checkbox,
  ConfidenceBar,
  DateTimeInput,
  Empty,
  Light,
  PageTabs,
  PageTitle,
  QueryState,
  Tag,
  Time,
  useStateLabel,
  useSubPage,
} from "../components/ui";
import { duration, fileSize } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { HourlyFiles } from "./HourlyFiles";

const SEGMENT_STATES = ["new", "queued", "done", "private", "failed"] as const;

const columns = (
  t: I18n["t"],
  stateLabel: (s: string) => string,
  cameraName: (id: string) => string,
): Column<Segment>[] => [
  {
    id: "thumb",
    header: t("col.preview"),
    width: 120,
    phone: "thumb",
    cell: (r) => r.thumb_url && <img className="mini" loading="lazy" src={r.thumb_url} alt="" />,
  },
  {
    id: "start",
    header: t("col.start"),
    width: 205,
    minWidth: 200,
    cell: (r) => <Time iso={r.start_at} />,
    compare: byString((r) => r.start_at),
  },
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (r) => <Tag>{cameraName(r.camera_id)}</Tag>,
    compare: byString((r) => cameraName(r.camera_id)),
  },
  {
    id: "length",
    header: t("col.length"),
    width: 80,
    cell: (r) => duration(r.duration_s),
    compare: byNumber((r) => r.duration_s),
  },
  {
    id: "size",
    header: t("col.size"),
    width: 90,
    cell: (r) => fileSize(r.size_bytes),
    compare: byNumber((r) => r.size_bytes),
  },
  {
    id: "light",
    header: t("col.light"),
    width: 90,
    cell: (r) => <Light night={r.is_night} long />,
    compare: byNumber((r) => (r.is_night === null ? -1 : Number(r.is_night))),
  },
  {
    id: "status",
    header: t("col.status"),
    width: 120,
    compare: byString((r) => r.status),
    cell: (r) => (
      <span className="row">
        <Tag kind={r.status} title={r.error ?? undefined}>
          {stateLabel(r.status)}
        </Tag>
        {r.pinned && (
          <Tooltip content={t("recordings.pinnedTooltip")} relationship="label">
            <Pin16Filled />
          </Tooltip>
        )}
        {r.archived && (
          <Tooltip content={t("recordings.inHourlyFile")} relationship="label">
            <Archive16Regular />
          </Tooltip>
        )}
      </span>
    ),
  },
  {
    id: "match",
    header: t("col.match"),
    width: 110,
    cell: (r) =>
      r.has_target && r.max_confidence !== null ? (
        <ConfidenceBar value={r.max_confidence} compact />
      ) : (
        ""
      ),
    compare: byNumber((r) => r.max_confidence),
  },
];

export function Recordings() {
  const [f, setFilter] = useFilters([
    "camera",
    "day",
    "only_targets",
    "status",
    "motion",
    "selected",
    "file",
  ] as const);
  const navigate = useNavigate();
  const tab = useSubPage("/recordings", ["archives", "segments"] as const);
  const segments = useQuery({
    queryKey: ["segments", f.camera, f.day, f.only_targets, f.status, f.motion],
    enabled: tab === "segments",
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.segments({
        camera: f.camera,
        day: f.day,
        only_targets: f.only_targets === "true",
        status: f.status || undefined,
        motion: f.motion === "yes" || f.motion === "no" ? f.motion : undefined,
        limit: 500,
      }),
    refetchInterval: 20_000,
  });
  const selectedId = Number(f.selected) || null;
  // The selected recording may not be in the filtered list (e.g. opened from a link).
  const fromList = segments.data?.find((s) => s.id === selectedId);
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  const cameraName = useCameraName();

  return (
    <>
      <PageTitle title={t("nav.recordings")} />
      <PageTabs
        base="/recordings"
        current={tab}
        tabs={[
          { id: "archives", label: t("hourly.title") },
          { id: "segments", label: t("recordings.segmentsTab") },
        ]}
      />
      {tab === "archives" ? (
        <>
          <div className="filters">
            <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
            <DateTimeInput
              type="date"
              value={f.day}
              placeholder={t("recordings.anyDay")}
              aria-label={t("recordings.dayUtc")}
              onChange={(v) => setFilter("day", v)}
            />
          </div>
          <HourlyFiles
            camera={f.camera}
            day={f.day}
            selectedId={Number(f.file) || null}
            onSelect={(id) => setFilter("file", id ? String(id) : "")}
            onShowSegments={(a) =>
              void navigate(
                `/recordings/segments?camera=${a.camera_id}&day=${a.start_at.slice(0, 10)}`,
              )
            }
          />
        </>
      ) : (
        <>
          <div className="filters">
            <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
            <DateTimeInput
              type="date"
              value={f.day}
              placeholder={t("recordings.anyDay")}
              aria-label={t("recordings.dayUtc")}
              onChange={(v) => setFilter("day", v)}
            />
            <Select
              value={f.status}
              aria-label={t("col.status")}
              onChange={(_, d) => setFilter("status", d.value)}
            >
              <option value="">{t("recordings.allStates")}</option>
              {SEGMENT_STATES.map((st) => (
                <option key={st} value={st}>
                  {stateLabel(st)}
                </option>
              ))}
            </Select>
            <Select
              value={f.motion}
              aria-label={t("recordings.motion")}
              onChange={(_, d) => setFilter("motion", d.value)}
            >
              <option value="">{t("recordings.anyMotion")}</option>
              <option value="yes">{t("recordings.withMotion")}</option>
              <option value="no">{t("recordings.withoutMotion")}</option>
            </Select>
            <Checkbox
              label={t("recordings.onlyWithMatch")}
              checked={f.only_targets === "true"}
              onChange={(_, d) => setFilter("only_targets", d.checked === true)}
            />
          </div>
          <QueryState error={segments.error} loading={segments.isPending} />
          <div className="master-detail">
            <div className="master">
              {segments.data && segments.data.length > 0 && (
                <DataTable
                  ariaLabel={t("recordings.segmentsTab")}
                  items={segments.data}
                  columns={columns(t, stateLabel, cameraName)}
                  getRowId={(r) => r.id}
                  defaultSort={{ column: "start", direction: "descending" }}
                  rowClassName={(r) => (r.has_target ? "target" : undefined)}
                  selectedId={selectedId}
                  onRowClick={(r) => setFilter("selected", String(r.id))}
                />
              )}
              {segments.data?.length === 0 && <Empty>{t("recordings.none")}</Empty>}
            </div>
            <SegmentPanel
              segmentId={selectedId}
              segment={fromList}
              onClose={() => setFilter("selected", "")}
            />
          </div>
        </>
      )}
    </>
  );
}
