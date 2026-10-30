// "Raw data" at the bottom of a detection's, an event's and a recording run's drawer
// section: what the worker posted (the same shape for every model) as facts and a table,
// how it was stored, and the JSON itself with a copy button. Fetched when first opened.
import { Button, Caption1 } from "@fluentui/react-components";
import { Copy20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useQuery, type QueryKey } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import {
  api,
  type DetectionEvent,
  type DetectionRaw,
  type EventRaw,
  type PostedDetection,
} from "../api";
import { decimal, duration } from "../format";
import { useI18n } from "../i18n";
import { DataTable, type Column } from "./DataTable";
import { useDetectorNames } from "./ProfileSelect";
import { Fact, Facts, QueryState, Tag, Time, useStateLabel } from "./ui";

/** Exact values: the raw view is for comparing, not reading at a glance. */
const exact = (v: number) => decimal(v, 4);
/** x; y; w; h, wrapping only between the numbers. */
const box = (b: number[] | null | undefined) =>
  b ? (
    <span className="mono">
      {b.map((v, i) => (
        <Fragment key={i}>
          {i > 0 && " "}
          <span className="nowrap">
            {exact(v)}
            {i < b.length - 1 && ";"}
          </span>
        </Fragment>
      ))}
    </span>
  ) : (
    "–"
  );
const seconds = (ms: number) => `${decimal(ms / 1000, 3)} s`;

/** The collapsed "Raw data" box; `children` gets what `load` fetched once it is opened. */
function RawSection<T>({
  label,
  queryKey,
  load,
  children,
}: {
  label?: string;
  queryKey: QueryKey;
  load: () => Promise<T>;
  children: (data: T) => ReactNode;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const raw = useQuery({
    queryKey,
    queryFn: load,
    enabled: open,
    // Switching items in the drawer keeps the previous one until the new one arrives.
    placeholderData: keepPreviousData,
  });
  return (
    <details className="helper raw-data" onToggle={(ev) => setOpen(ev.currentTarget.open)}>
      <summary>{label ?? t("raw.title")}</summary>
      {raw.data === undefined ? (
        <QueryState loading={raw.isFetching} error={raw.error} />
      ) : (
        children(raw.data)
      )}
    </details>
  );
}

/** The JSON itself, collapsed, with a button that copies it. */
export function JsonView({ value }: { value: unknown }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(value, null, 2);
  return (
    <details className="raw-result">
      <summary>
        <Caption1>{t("raw.json")}</Caption1>
      </summary>
      <div className="form-stack">
        <pre>{text}</pre>
        <span className="row">
          <Button
            size="small"
            icon={<Copy20Regular />}
            onClick={() => void navigator.clipboard?.writeText(text).then(() => setCopied(true))}
          >
            {copied ? t("share.copied") : t("share.copy")}
          </Button>
        </span>
      </div>
    </details>
  );
}

interface PostedRow extends PostedDetection {
  key: string | number;
  /** Stored detections: their time, detector and id (opens it). */
  ts?: string;
  detector_id?: string | null;
  detection_id?: number;
}

/** What the worker posted, one row per detection: exact values, nothing rounded. */
function PostedTable({ rows, onOpen }: { rows: PostedRow[]; onOpen?: (row: PostedRow) => void }) {
  const { t } = useI18n();
  const detectorNames = useDetectorNames();
  const stored = rows.some((r) => r.ts);
  const columns: Column<PostedRow>[] = [
    {
      id: "time",
      header: stored ? t("col.time") : t("raw.at"),
      cell: (r) => (r.ts ? <Time iso={r.ts} /> : seconds(r.offset_ms)),
      compare: (a, b) => a.offset_ms - b.offset_ms || String(a.ts).localeCompare(String(b.ts)),
      width: stored ? 150 : 140,
    },
    { id: "label", header: t("col.label"), cell: (r) => r.label, width: 110 },
    {
      id: "confidence",
      header: t("col.confidence"),
      cell: (r) => exact(r.confidence),
      compare: (a, b) => a.confidence - b.confidence,
      width: 90,
    },
    ...(stored
      ? [
          {
            id: "detector",
            header: t("col.detector"),
            cell: (r: PostedRow) =>
              r.detector_id ? (detectorNames.get(r.detector_id) ?? r.detector_id) : "–",
            width: 110,
          },
        ]
      : []),
    {
      id: "box",
      header: t("detections.box"),
      cell: (r) => box(r.bbox),
      width: 220,
    },
  ];
  return (
    <DataTable
      items={rows}
      columns={columns}
      getRowId={(r) => r.key}
      ariaLabel={t("raw.posted")}
      onRowClick={onOpen}
    />
  );
}

/** The run that found a detection: its state, model, worker and time. */
function RunFacts({ job }: { job: NonNullable<DetectionRaw["job"]> }) {
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  return (
    <>
      <Fact label={t("col.job")} className="row">
        <span>#{job.id}</span>
        <Tag kind={job.state}>{stateLabel(job.state)}</Tag>
      </Fact>
      <Fact label={t("col.model")}>{job.model ?? "–"}</Fact>
      <Fact label={t("col.worker")}>{job.worker_id ?? "–"}</Fact>
      <Fact label={t("raw.processing")}>
        {job.processing_ms !== null ? duration(job.processing_ms / 1000) : "–"}
      </Fact>
      <Fact label={t("raw.finished")}>
        <Time iso={job.finished_at} />
      </Fact>
    </>
  );
}

/** A detection: what the worker posted for it (label before aliases, exact values) and
 *  the run that found it. */
export function DetectionRawData({ id }: { id: number }) {
  const { t } = useI18n();
  return (
    <RawSection queryKey={["detection-raw", id]} load={() => api.detectionRaw(id)}>
      {(raw) => (
        <div className="form-stack">
          {raw.posted ? (
            <Facts>
              <Fact label={t("col.label")}>
                {raw.posted.label !== raw.stored.label
                  ? t("raw.storedAs", { posted: raw.posted.label, stored: raw.stored.label })
                  : raw.posted.label}
              </Fact>
              <Fact label={t("col.confidence")}>{exact(raw.posted.confidence)}</Fact>
              <Fact label={t("raw.at")}>{seconds(raw.posted.offset_ms)}</Fact>
              <Fact label={t("detections.box")}>{box(raw.posted.bbox)}</Fact>
              {raw.job && <RunFacts job={raw.job} />}
            </Facts>
          ) : (
            <>
              <Caption1 className="muted">
                {raw.job?.truncated ? t("raw.tooBig") : t("raw.notPosted")}
              </Caption1>
              {raw.job && (
                <Facts>
                  <RunFacts job={raw.job} />
                </Facts>
              )}
            </>
          )}
          <JsonView value={raw} />
        </div>
      )}
    </RawSection>
  );
}

/** An event: when it was last seen, how many detectors agreed, and its detections as the
 *  workers posted them (a row opens the detection). */
export function EventRawData({ event: e }: { event: DetectionEvent }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const rows = (raw: EventRaw): PostedRow[] =>
    raw.detections.map((d) => ({
      // Not kept (a result too big): the stored values stand in.
      ...(d.posted ?? {
        offset_ms: 0,
        label: d.stored.label,
        confidence: Number(d.stored.confidence),
        bbox: null,
      }),
      key: d.stored.id,
      ts: d.stored.ts,
      detector_id: d.stored.detector_id,
      detection_id: d.stored.id,
    }));
  return (
    <RawSection queryKey={["event-raw", e.id]} load={() => api.eventRaw(e.id)}>
      {(raw) => (
        <div className="form-stack">
          <Facts>
            <Fact label={t("raw.lastSeen")}>
              <Time iso={raw.stored.last_ts} />
            </Fact>
            <Fact label={t("raw.agreement")}>{raw.stored.agreement ?? "–"}</Fact>
          </Facts>
          <PostedTable
            rows={rows(raw)}
            onOpen={(r) => void navigate(`/events/detections?event=${e.id}&selected=${r.key}`)}
          />
          {raw.more_detections && (
            <Caption1 className="muted">
              {t("raw.firstDetections", { n: raw.detections.length })}
            </Caption1>
          )}
          <JsonView value={raw} />
        </div>
      )}
    </RawSection>
  );
}

/** A recording's run: everything the worker posted, also what was too weak to keep. */
export function RunRawData({ result }: { result: unknown }) {
  const { t } = useI18n();
  const posted =
    result && typeof result === "object" && "detections" in result
      ? (result as { detections: PostedDetection[] }).detections
      : null;
  return (
    <details className="helper raw-data">
      <summary>{t("recordings.rawResult")}</summary>
      <div className="form-stack">
        {posted === null ? (
          <Caption1 className="muted">{t("raw.tooBig")}</Caption1>
        ) : posted.length === 0 ? (
          <Caption1 className="muted">{t("raw.nothingPosted")}</Caption1>
        ) : (
          <>
            <Caption1 className="muted">{t("raw.postedHint")}</Caption1>
            <PostedTable rows={posted.map((d, i) => ({ ...d, key: i }))} />
          </>
        )}
        <JsonView value={result} />
      </div>
    </details>
  );
}
