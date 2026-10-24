import { Button, Caption1, Checkbox, Input, Select, Text } from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  Folder20Regular,
  ScanObject20Regular,
} from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment } from "react";

import { api, detectionFrameUrl, segmentPlayUrl, type Detection } from "../api";
import { CameraSelect, useCameraName } from "../components/CameraSelect";
import { ProfileSelect, useDetectorNames, useSeveralDetectors } from "../components/ProfileSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import { Feedback } from "../components/Feedback";
import {
  ConfidenceBar,
  Empty,
  InfoBanner,
  Light,
  PageTitle,
  QueryState,
  Time,
  Toolbar,
  useRouterHref,
} from "../components/ui";
import { decimal, percent } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";

const columns = (t: I18n["t"], cameraName: (id: string) => string): Column<Detection>[] => [
  {
    id: "thumb",
    header: t("col.preview"),
    width: 120,
    phone: "thumb",
    // The frame with the box drawn in; made on first view (lazy: rows in sight only).
    cell: (d) => (
      <img className="mini" loading="lazy" src={detectionFrameUrl(d.id, "small")} alt="" />
    ),
  },
  {
    id: "ts",
    header: t("col.time"),
    width: 205,
    minWidth: 200,
    cell: (d) => <Time iso={d.ts} />,
    compare: byString((d) => d.ts),
  },
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (d) => cameraName(d.camera_id),
    compare: byString((d) => cameraName(d.camera_id)),
  },
  {
    id: "label",
    header: t("col.label"),
    width: 130,
    cell: (d) => <Text weight="semibold">{d.label}</Text>,
    compare: byString((d) => d.label),
  },
  {
    id: "confidence",
    header: t("col.confidence"),
    width: 150,
    cell: (d) => <ConfidenceBar value={d.confidence} />,
    compare: byNumber((d) => d.confidence),
  },
  { id: "light", header: t("col.light"), width: 70, cell: (d) => <Light night={d.is_night} /> },
  {
    id: "model",
    header: t("col.modelWorker"),
    width: 230,
    cell: (d) => (
      <Caption1 title={`${d.model ?? "–"} / ${d.worker_id ?? ""}`}>
        {d.model ?? "–"} <span className="muted">/ {d.worker_id}</span>
      </Caption1>
    ),
    compare: byString((d) => d.model),
  },
  {
    id: "feedback",
    header: t("col.correct"),
    width: 100,
    cell: (d) => <Feedback detection={d} />,
    compare: byNumber((d) => (d.feedback === null ? 0 : d.feedback ? 2 : 1)),
  },
];

export function Detections() {
  const [f, setFilter] = useFilters([
    "camera",
    "profile",
    "label",
    "only_targets",
    "min_confidence",
    "detector",
    "selected",
  ] as const);
  const query = {
    camera: f.camera,
    profile: f.profile,
    detector: f.detector || undefined,
    label: f.label,
    only_targets: f.only_targets === "true",
    min_confidence: Number(f.min_confidence) || 0,
    limit: 500,
  };
  const detections = useQuery({
    queryKey: ["detections", query],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () => api.detections(query),
    refetchInterval: 20_000,
  });
  const selected = detections.data?.find((d) => String(d.id) === f.selected);
  const { t } = useI18n();
  const routerHref = useRouterHref();
  const cameraName = useCameraName();
  const severalDetectors = useSeveralDetectors();
  const detectorNames = useDetectorNames();

  return (
    <>
      <PageTitle title={t("nav.detections")} />
      <Toolbar>
        <Button as="a" icon={<ScanObject20Regular />} {...routerHref("/cameras/profiles")}>
          {t("profiles.title")}
        </Button>
        <Button as="a" icon={<ArrowDownload20Regular />} href="/api/detections/feedback-export">
          {t("detections.exportJson")}
        </Button>
      </Toolbar>
      <InfoBanner id="detections">{t("detections.intro")}</InfoBanner>
      <div className="filters">
        <CameraSelect value={f.camera} onChange={(v) => setFilter("camera", v)} />
        <ProfileSelect value={f.profile} onChange={(v) => setFilter("profile", v)} />
        {(severalDetectors || f.detector) && (
          <Select
            value={f.detector}
            aria-label={t("col.detector")}
            onChange={(_, d) => setFilter("detector", d.value)}
          >
            <option value="">{t("detections.allDetectors")}</option>
            {[...detectorNames].map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        )}
        <Input
          placeholder={t("detections.labelPlaceholder")}
          aria-label={t("col.label")}
          defaultValue={f.label}
          onKeyDown={(e) => e.key === "Enter" && setFilter("label", e.currentTarget.value.trim())}
          onBlur={(e) => setFilter("label", e.currentTarget.value.trim())}
        />
        <Input
          type="number"
          className="narrow"
          min={0}
          max={1}
          step={0.05}
          aria-label={t("detections.minConfidence")}
          contentBefore={<Caption1 className="muted">{t("detections.min")}</Caption1>}
          value={f.min_confidence || "0"}
          onChange={(_, d) => setFilter("min_confidence", d.value === "0" ? "" : d.value)}
        />
        <Checkbox
          label={t("detections.onlyTargets")}
          checked={f.only_targets === "true"}
          onChange={(_, d) => setFilter("only_targets", d.checked === true)}
        />
      </div>
      <QueryState error={detections.error} loading={detections.isPending} />
      <div className="master-detail">
        <div className="master">
          {detections.data && detections.data.length > 0 && (
            <DataTable
              ariaLabel="Detections"
              items={detections.data}
              columns={columns(t, cameraName)}
              getRowId={(d) => d.id}
              defaultSort={{ column: "ts", direction: "descending" }}
              rowClassName={(d) => (d.is_target ? "target" : undefined)}
              selectedId={selected?.id}
              onRowClick={(d) => setFilter("selected", String(d.id))}
            />
          )}
          {detections.data?.length === 0 && <Empty>{t("detections.none")}</Empty>}
        </div>
        <DetailPanel
          open={!!selected}
          title={selected ? `${selected.label} – ${percent(selected.confidence, 1)}` : ""}
          onClose={() => setFilter("selected", "")}
        >
          {selected && <DetectionDetail detection={selected} />}
        </DetailPanel>
      </div>
    </>
  );
}

function DetectionDetail({ detection: d }: { detection: Detection }) {
  const routerHref = useRouterHref();
  const cameraName = useCameraName();
  const { t } = useI18n();
  return (
    <div className="detail-stack">
      <img
        className="detail-media"
        src={detectionFrameUrl(d.id)}
        alt={t("detections.frameAlt", { label: d.label })}
      />
      <Caption1 className="muted">{t("detections.frameNote")}</Caption1>
      <Feedback detection={d} withLabels />
      <dl className="facts">
        <dt>{t("col.time")}</dt>
        <dd>
          <Time iso={d.ts} />
        </dd>
        <dt>{t("common.camera")}</dt>
        <dd>{cameraName(d.camera_id)}</dd>
        <dt>{t("col.confidence")}</dt>
        <dd>
          <ConfidenceBar value={d.confidence} />
        </dd>
        <dt>{t("detections.target")}</dt>
        <dd>{d.is_target ? t("common.yes") : t("common.no")}</dd>
        <dt>{t("col.light")}</dt>
        <dd>
          <Light night={d.is_night} long />
        </dd>
        <dt>{t("detections.box")}</dt>
        <dd className="mono">{d.bbox ? d.bbox.map((v) => decimal(v, 3)).join("; ") : "–"}</dd>
        <dt>{t("col.model")}</dt>
        <dd>{d.model ?? "–"}</dd>
        {d.extra.classifications && d.extra.classifications.length > 0 && (
          <>
            <dt>{t("detections.topGuesses")}</dt>
            <dd>
              {d.extra.classifications.map((g) => `${g.label} ${decimal(g.score, 2)}`).join(" – ")}
            </dd>
          </>
        )}
        {/* Everything else the detector reported (e.g. raw_label, its own box label). */}
        {Object.entries(d.extra)
          .filter(([key]) => key !== "classifications")
          .map(([key, value]) => (
            <Fragment key={key}>
              <dt className="mono">{key}</dt>
              <dd className="mono extra-value">
                {typeof value === "string" ? value : JSON.stringify(value)}
              </dd>
            </Fragment>
          ))}
        <dt>{t("col.worker")}</dt>
        <dd>{d.worker_id ?? "–"}</dd>
      </dl>
      {/* key: reload the player when switching detections */}
      <video
        key={d.id}
        className="detail-media"
        src={segmentPlayUrl(d.segment_id, d.offset_ms)}
        controls
        playsInline
        preload="metadata"
      />
      <Button
        as="a"
        icon={<Folder20Regular />}
        {...routerHref(`/recordings?selected=${d.segment_id}`)}
      >
        {t("detections.openRecording")}
      </Button>
    </div>
  );
}
