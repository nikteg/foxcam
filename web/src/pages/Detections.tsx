import { Button, Caption1, Input, Select, Text } from "@fluentui/react-components";
import { Dismiss16Regular, Folder20Regular, ScanObject20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment } from "react";

import { api, detectionFrameUrl, type Detection } from "../api";
import { CameraSelect, CameraTag, useCameraName } from "../components/CameraSelect";
import { ProfileSelect, useDetectorNames, useSeveralDetectors } from "../components/ProfileSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import { Feedback } from "../components/Feedback";
import {
  Checkbox,
  ConfidenceBar,
  DownloadButton,
  DrawerTitle,
  Empty,
  Fact,
  Facts,
  InfoBanner,
  Light,
  QueryState,
  Tag,
  Time,
  Toolbar,
  useRouterHref,
} from "../components/ui";
import { decimal, localTime } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { VideoPlayer } from "../components/VideoPlayer";
import { DetectionMotion } from "../components/Motion";
import { TableSkeleton } from "../components/Skeletons";

const columns = (t: I18n["t"], cameraName: (id: string) => string): Column<Detection>[] => [
  {
    id: "thumb",
    header: t("col.preview"),
    width: 120,
    phone: "thumb",
    // The frame with the box drawn in; made on first view (lazy: rows in sight only).
    cell: (d) => <img className="mini" loading="lazy" src={detectionFrameUrl(d, "small")} alt="" />,
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
    cell: (d) => <CameraTag id={d.camera_id} />,
    compare: byString((d) => cameraName(d.camera_id)),
  },
  {
    id: "label",
    header: t("col.label"),
    width: 130,
    cell: (d) => (
      <span className="row">
        <Text weight="semibold">{d.label}</Text>
        {d.still && <Tag title={t("detections.stillHint")}>{t("detections.still")}</Tag>}
      </span>
    ),
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

/** Events → Detections: every single hit, the evidence behind the events. */
export function DetectionList() {
  const [f, setFilter] = useFilters([
    "camera",
    "profile",
    "label",
    "only_targets",
    "still",
    "min_confidence",
    "detector",
    "event",
    "selected",
  ] as const);
  const query = {
    camera: f.camera,
    profile: f.profile,
    detector: f.detector || undefined,
    event: Number(f.event) || undefined,
    label: f.label,
    only_targets: f.only_targets === "true",
    // Targets standing still are no match: hidden unless asked for (they pile up).
    hide_still: f.still !== "true",
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
      <Toolbar>
        <Button as="a" icon={<ScanObject20Regular />} {...routerHref("/cameras/profiles")}>
          {t("profiles.title")}
        </Button>
        <DownloadButton href="/api/detections/feedback-export">
          {t("detections.exportJson")}
        </DownloadButton>
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
          max={100}
          step={5}
          aria-label={t("detections.showFrom")}
          contentBefore={<Caption1 className="muted">{t("detections.showFromShort")}</Caption1>}
          contentAfter="%"
          // In the URL as 0–1, like the API.
          value={String(Math.round((Number(f.min_confidence) || 0) * 100))}
          onChange={(_, d) =>
            setFilter("min_confidence", Number(d.value) > 0 ? String(Number(d.value) / 100) : "")
          }
        />
        {f.event && (
          <Button
            icon={<Dismiss16Regular />}
            iconPosition="after"
            title={t("detections.eventClear")}
            onClick={() => setFilter("event", "")}
          >
            {t("detections.eventFilter", { id: f.event })}
          </Button>
        )}
        <Checkbox
          label={t("detections.onlyTargets")}
          checked={f.only_targets === "true"}
          onChange={(_, d) => setFilter("only_targets", d.checked === true)}
        />
        <Checkbox
          label={t("detections.showStill")}
          checked={f.still === "true"}
          onChange={(_, d) => setFilter("still", d.checked === true)}
        />
      </div>
      <QueryState
        error={detections.error}
        loading={detections.isPending}
        skeleton={<TableSkeleton thumb />}
      />
      <div className="master-detail">
        <div className="master">
          {detections.data && detections.data.length > 0 && (
            <DataTable
              ariaLabel={t("nav.detections")}
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
          title={
            selected && (
              <DrawerTitle
                title={selected.label}
                sub={`${cameraName(selected.camera_id)} – ${localTime(selected.ts)}`}
              />
            )
          }
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
  const { t } = useI18n();
  return (
    <div className="detail-stack">
      <img
        className="detail-media"
        src={detectionFrameUrl(d)}
        alt={t("detections.frameAlt", { label: d.label })}
      />
      <Caption1 className="muted">{t("detections.frameNote")}</Caption1>
      <Feedback detection={d} withLabels />
      <Facts>
        <Fact label={t("col.time")}>
          <Time iso={d.ts} />
        </Fact>
        <Fact label={t("common.camera")}>
          <CameraTag id={d.camera_id} />
        </Fact>
        <Fact label={t("col.confidence")}>
          <ConfidenceBar value={d.confidence} />
        </Fact>
        <Fact label={t("detections.target")}>{d.is_target ? t("common.yes") : t("common.no")}</Fact>
        <Fact label={t("col.light")}>
          <Light night={d.is_night} long />
        </Fact>
        <Fact label={t("detections.motion")}>
          <DetectionMotion detection={d} />
        </Fact>
        <Fact label={t("detections.box")} className="mono">
          {d.bbox ? d.bbox.map((v) => decimal(v, 3)).join("; ") : "–"}
        </Fact>
        <Fact label={t("col.model")}>{d.model ?? "–"}</Fact>
        {d.extra.classifications && d.extra.classifications.length > 0 && (
          <>
            <Fact label={t("detections.topGuesses")}>
              {d.extra.classifications.map((g) => `${g.label} ${decimal(g.score, 2)}`).join(" – ")}
            </Fact>
          </>
        )}
        {/* Everything else the detector reported (e.g. raw_label, its own box label). */}
        {Object.entries(d.extra)
          .filter(([key]) => key !== "classifications")
          .map(([key, value]) => (
            <Fragment key={key}>
              <Fact label={key} labelClassName="mono" className="mono extra-value">
                {typeof value === "string" ? value : JSON.stringify(value)}
              </Fact>
            </Fragment>
          ))}
        <Fact label={t("col.worker")}>{d.worker_id ?? "–"}</Fact>
      </Facts>
      <div className="detail-media">
        <VideoPlayer
          title={d.label}
          src={`/api/segments/${d.segment_id}/play`}
          startAt={Math.max(d.offset_ms / 1000 - 1, 0)}
          thumbnails={`/api/segments/${d.segment_id}/thumbnails.vtt`}
        />
      </div>
      <Button
        as="a"
        icon={<Folder20Regular />}
        {...routerHref(`/recordings/segments?selected=${d.segment_id}`)}
      >
        {t("detections.openRecording")}
      </Button>
    </div>
  );
}
