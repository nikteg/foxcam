import {
  Button,
  Caption1,
  Checkbox,
  Input,
  Link,
  MessageBar,
  MessageBarBody,
  Select,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import {
  Archive16Regular,
  ArrowClockwise20Regular,
  ArrowDownload20Regular,
  Pin16Filled,
} from "@fluentui/react-icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api, type Segment, type SegmentJob } from "../api";
import { CameraSelect, useCameraName } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import { Feedback } from "../components/Feedback";
import {
  Empty,
  Light,
  PageHeader,
  PageTabs,
  QueryState,
  Tag,
  Time,
  useStateLabel,
  useSubPage,
} from "../components/ui";
import { decimal, duration, fileSize, localTime, percent } from "../format";
import { useI18n, type I18n } from "../i18n";
import { hasRole, useCameraRole } from "../auth";
import { useFilters } from "../useFilters";
import { HourlyFiles } from "./HourlyFiles";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";

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
    cell: (r) => cameraName(r.camera_id),
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
    width: 80,
    cell: (r) => (r.has_target && r.max_confidence !== null ? percent(r.max_confidence) : ""),
    compare: byNumber((r) => r.max_confidence),
  },
];

export function Recordings() {
  const [f, setFilter] = useFilters([
    "camera",
    "day",
    "only_targets",
    "status",
    "selected",
    "file",
  ] as const);
  const navigate = useNavigate();
  const tab = useSubPage("/recordings", ["segments", "joined"] as const);
  const segments = useQuery({
    queryKey: ["segments", f.camera, f.day, f.only_targets, f.status],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () =>
      api.segments({
        camera: f.camera,
        day: f.day,
        only_targets: f.only_targets === "true",
        status: f.status || undefined,
        limit: 500,
      }),
    refetchInterval: 20_000,
  });
  const selectedId = Number(f.selected) || null;
  // The selected recording may not be in the filtered list (e.g. opened from a link).
  const fromList = segments.data?.find((s) => s.id === selectedId);
  const single = useQuery({
    queryKey: ["segment", selectedId],
    queryFn: () => api.segment(selectedId!),
    enabled: selectedId !== null && !fromList,
  });
  const selected = fromList ?? single.data;
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  const cameraName = useCameraName();

  return (
    <>
      <PageHeader title={t("nav.recordings")} />
      <PageTabs
        base="/recordings"
        current={tab}
        tabs={[
          { id: "segments", label: t("recordings.segmentsTab") },
          { id: "joined", label: t("hourly.title") },
        ]}
      />
      {tab === "joined" ? (
        <>
          <div className="filters">
            <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
            <Input
              type="date"
              value={f.day}
              aria-label={t("recordings.dayUtc")}
              onChange={(_, d) => setFilter("day", d.value)}
            />
          </div>
          <HourlyFiles
            camera={f.camera}
            day={f.day}
            selectedId={Number(f.file) || null}
            onSelect={(id) => setFilter("file", id ? String(id) : "")}
            onShowSegments={(a) =>
              void navigate(`/recordings?camera=${a.camera_id}&day=${a.start_at.slice(0, 10)}`)
            }
          />
        </>
      ) : (
        <>
          <div className="filters">
            <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
            <Input
              type="date"
              value={f.day}
              aria-label={t("recordings.dayUtc")}
              onChange={(_, d) => setFilter("day", d.value)}
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
                  ariaLabel="Recordings"
                  items={segments.data}
                  columns={columns(t, stateLabel, cameraName)}
                  getRowId={(r) => r.id}
                  defaultSort={{ column: "start", direction: "descending" }}
                  rowClassName={(r) => (r.has_target ? "target" : undefined)}
                  selectedId={selected?.id}
                  onRowClick={(r) => setFilter("selected", String(r.id))}
                />
              )}
              {segments.data?.length === 0 && <Empty>{t("recordings.none")}</Empty>}
            </div>
            <DetailPanel
              open={!!selected}
              title={
                selected
                  ? `${cameraName(selected.camera_id)} – ${localTime(selected.start_at)}`
                  : ""
              }
              onClose={() => setFilter("selected", "")}
            >
              {selected && (
                <RecordingDetail
                  segment={selected}
                  onOpenHourly={(id) => void navigate(`/recordings/joined?file=${id}`)}
                />
              )}
            </DetailPanel>
          </div>
        </>
      )}
    </>
  );
}

function RecordingDetail({
  segment: s,
  onOpenHourly,
}: {
  segment: Segment;
  onOpenHourly: (archiveId: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const { t } = useI18n();
  const role = useCameraRole(s.camera_id);
  const canEdit = role !== undefined && hasRole({ role }, "editor");
  const [confirming, setConfirming] = useState(false);
  const stateLabel = useStateLabel();
  const detections = useQuery({
    queryKey: ["detections", { segment: s.id }],
    queryFn: () => api.detections({ segment: s.id, limit: 500 }),
  });
  const jobs = useQuery({
    queryKey: ["segment-jobs", s.id],
    queryFn: () => api.segmentJobs(s.id),
  });
  const seek = (ms: number) => {
    if (!video.current) return;
    video.current.currentTime = ms / 1000;
    void video.current.play();
    // The detections list is below the player: bring the player into view (only scrolls
    // when it is not already visible).
    video.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <div className="detail-stack">
      {s.deleted ? (
        <Empty>{t("recordings.deleted")}</Empty>
      ) : (
        <video
          key={s.id}
          ref={video}
          className="detail-media"
          src={`/api/segments/${s.id}/play`}
          poster={s.thumb_url ?? undefined}
          controls
          playsInline
          preload="metadata"
        />
      )}
      <div className="row wrap">
        {s.download_url && (
          <Button
            as="a"
            href={s.download_url}
            appearance="primary"
            icon={<ArrowDownload20Regular />}
          >
            {t("common.download")}
          </Button>
        )}
        <Tag kind={s.status} title={s.error ?? undefined}>
          {stateLabel(s.status)}
        </Tag>
        {s.pinned && <Tag kind="ok">{t("recordings.keptForEvent")}</Tag>}
        {canEdit && !s.deleted && (
          <Button
            icon={<ArrowClockwise20Regular />}
            disabled={s.status === "new"}
            onClick={() => setConfirming(true)}
          >
            {t("recordings.analyseAgain")}
          </Button>
        )}
      </div>
      {confirming && <ReanalyseDialog segment={s} onClose={() => setConfirming(false)} />}
      <dl className="facts">
        <dt>{t("col.start")}</dt>
        <dd>
          <Time iso={s.start_at} />
        </dd>
        <dt>{t("col.length")}</dt>
        <dd>{duration(s.duration_s)}</dd>
        <dt>{t("col.size")}</dt>
        <dd>{fileSize(s.size_bytes)}</dd>
        <dt>{t("col.light")}</dt>
        <dd className="row">
          <Light night={s.is_night} long />
          {s.saturation_avg !== null && (
            <span className="muted">
              ({t("recordings.saturation", { value: decimal(s.saturation_avg, 1) })})
            </span>
          )}
        </dd>
        <dt>{t("recordings.sunElevation")}</dt>
        <dd>{s.sun_elevation !== null ? `${decimal(s.sun_elevation, 1)}°` : "–"}</dd>
        <dt>{t("recordings.censorMask")}</dt>
        <dd>
          {s.mask_version_id
            ? t("recordings.maskVersion", { version: s.mask_version_id })
            : t("recordings.noMask")}
        </dd>
        <dt>{t("recordings.storedIn")}</dt>
        <dd>
          {s.archive_id !== null ? (
            <Link onClick={() => onOpenHourly(s.archive_id!)}>{t("recordings.storedHourly")}</Link>
          ) : (
            t("recordings.storedOwn")
          )}
        </dd>
      </dl>

      <Text weight="semibold">{t("recordings.runs")}</Text>
      {jobs.data?.length === 0 && <Caption1 className="muted">{t("recordings.noRuns")}</Caption1>}
      <ul className="run-list">
        {jobs.data?.map((j) => (
          <RunSummary key={j.id} job={j} />
        ))}
      </ul>

      <Text weight="semibold">{t("nav.detections")}</Text>
      {detections.data?.length === 0 && (
        <Caption1 className="muted">{t("recordings.noDetections")}</Caption1>
      )}
      <ul className="detection-list">
        {detections.data?.map((d) => (
          <li key={d.id} className={d.is_target ? "target" : undefined}>
            <Button size="small" appearance="subtle" onClick={() => seek(d.offset_ms)}>
              ▶ {decimal(d.offset_ms / 1000, 1)} s
            </Button>
            <Text weight="semibold">{d.label}</Text>
            <Caption1>{percent(d.confidence, 1)}</Caption1>
            <span className="spacer" />
            <Feedback detection={d} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "Fox – speciesnet:4.0.1 – done – 0 detections – 14s", plus the error of a failed run. */
function RunSummary({ job: j }: { job: SegmentJob }) {
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  const ran = j.state === "done";
  const parts = [
    ...(j.model ? [j.model] : []),
    ...(ran
      ? [
          j.detections === 0
            ? t("recordings.runNothing")
            : t("recordings.runFound", { n: j.detections, targets: j.targets }),
        ]
      : []),
    ...(j.processing_ms !== null ? [duration(j.processing_ms / 1000)] : []),
  ];
  return (
    <li>
      <span className="row wrap">
        <Text weight="semibold">
          {j.profile_name ?? j.profile_id ?? "–"} – {j.detector_name ?? j.detector_id ?? "–"}
        </Text>
        <Tag kind={j.state}>{stateLabel(j.state)}</Tag>
        {parts.length > 0 && <Caption1 className="muted">{parts.join(" – ")}</Caption1>}
      </span>
      {j.error && <Caption1 className="run-error">{j.error}</Caption1>}
      {j.raw_result !== undefined && j.raw_result !== null && (
        <details className="raw-result">
          <summary>
            <Caption1>{t("recordings.rawResult")}</Caption1>
          </summary>
          <pre>{JSON.stringify(j.raw_result, null, 2)}</pre>
        </details>
      )}
    </li>
  );
}

/** Confirm, then run detection on the recording again (its detections are replaced). */
function ReanalyseDialog({ segment: s, onClose }: { segment: Segment; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const run = useMutation({
    mutationFn: () => api.reanalyse(s.id),
    onSuccess: () => {
      for (const key of ["segments", "segment", "segment-jobs", "detections", "events", "queue"]) {
        void client.invalidateQueries({ queryKey: [key] });
      }
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("recordings.analyseAgainTitle")}</DialogTitle>
          <DialogContent className="form-stack">
            <Text>{t("recordings.analyseAgainBody")}</Text>
            {run.error && (
              <MessageBar intent="error">
                <MessageBarBody>{run.error.message}</MessageBarBody>
              </MessageBar>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button
              appearance="primary"
              icon={<ArrowClockwise20Regular />}
              disabled={run.isPending}
              onClick={() => run.mutate()}
            >
              {t("recordings.analyseAgain")}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
