import { Button, Caption1, Checkbox, Input, Text, Tooltip } from "@fluentui/react-components";
import { ArrowDownload20Regular, Pin16Filled } from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { api, type Segment } from "../api";
import { CameraSelect } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import { Feedback } from "../components/Feedback";
import { Empty, Light, PageHeader, QueryState, Tag, Time, useStateLabel } from "../components/ui";
import { decimal, duration, fileSize, localTime, percent } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";

const columns = (t: I18n["t"], stateLabel: (s: string) => string): Column<Segment>[] => [
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
    width: 110,
    cell: (r) => r.camera_id,
    compare: byString((r) => r.camera_id),
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
  const [f, setFilter] = useFilters(["camera", "day", "only_targets", "selected"] as const);
  const segments = useQuery({
    queryKey: ["segments", f.camera, f.day, f.only_targets],
    queryFn: () =>
      api.segments({
        camera: f.camera,
        day: f.day,
        only_targets: f.only_targets === "true",
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

  return (
    <>
      <PageHeader title={t("nav.recordings")} />
      <div className="filters">
        <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
        <Input
          type="date"
          value={f.day}
          aria-label={t("recordings.dayUtc")}
          onChange={(_, d) => setFilter("day", d.value)}
        />
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
              columns={columns(t, stateLabel)}
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
          title={selected ? `${selected.camera_id} · ${localTime(selected.start_at)}` : ""}
          onClose={() => setFilter("selected", "")}
        >
          {selected && <RecordingDetail segment={selected} />}
        </DetailPanel>
      </div>
    </>
  );
}

function RecordingDetail({ segment: s }: { segment: Segment }) {
  const video = useRef<HTMLVideoElement>(null);
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  const detections = useQuery({
    queryKey: ["detections", { segment: s.id }],
    queryFn: () => api.detections({ segment: s.id, limit: 500 }),
  });
  const seek = (ms: number) => {
    if (!video.current) return;
    video.current.currentTime = ms / 1000;
    void video.current.play();
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
      </div>
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
        <dd>
          <Light night={s.is_night} long />
          {s.saturation_avg !== null && (
            <span className="muted">
              {" "}
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
      </dl>

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
