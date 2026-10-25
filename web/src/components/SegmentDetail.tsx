import { Button, Caption1, Link, Text } from "@fluentui/react-components";
import { ArrowClockwise20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useNavigate } from "react-router";

import { api, type Segment, type SegmentJob } from "../api";
import { hasRole, useCameraRole } from "../auth";
import { decimal, duration, fileSize, localTime } from "../format";
import { useI18n } from "../i18n";
import { useCameraName } from "./CameraSelect";
import { ConfirmPopover } from "./ConfirmPopover";
import { DetailPanel } from "./DetailPanel";
import { Feedback } from "./Feedback";
import {
  ConfidenceBar,
  DownloadButton,
  DrawerTitle,
  Empty,
  Fact,
  Facts,
  Light,
  Tag,
  Time,
  useStateLabel,
} from "./ui";
import { VideoPlayer, type PlayerHandle } from "./VideoPlayer";

/**
 * A recording's detail drawer, for the recording whose id is in `segmentId`. Pass `segment`
 * when the page's list already has it; otherwise it is fetched (e.g. opened from a link).
 */
export function SegmentPanel({
  segmentId,
  segment,
  onClose,
}: {
  segmentId: number | null;
  segment?: Segment;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const cameraName = useCameraName();
  const single = useQuery({
    queryKey: ["segment", segmentId],
    queryFn: () => api.segment(segmentId!),
    enabled: segmentId !== null && !segment,
  });
  const selected = segmentId === null ? undefined : (segment ?? single.data);
  return (
    <DetailPanel
      open={!!selected}
      title={
        selected && (
          <DrawerTitle title={cameraName(selected.camera_id)} sub={localTime(selected.start_at)} />
        )
      }
      onClose={onClose}
    >
      {selected && (
        <SegmentDetail
          segment={selected}
          onOpenHourly={(id) => void navigate(`/recordings?file=${id}`)}
        />
      )}
    </DetailPanel>
  );
}

function SegmentDetail({
  segment: s,
  onOpenHourly,
}: {
  segment: Segment;
  onOpenHourly: (archiveId: number) => void;
}) {
  const player = useRef<PlayerHandle>(null);
  const playerBox = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const role = useCameraRole(s.camera_id);
  const canEdit = role !== undefined && hasRole({ role }, "editor");
  const client = useQueryClient();
  // Its detections are replaced: asks first.
  const reanalyse = useMutation({
    mutationFn: () => api.reanalyse(s.id),
    onSuccess: () => {
      for (const key of ["segments", "segment", "segment-jobs", "detections", "events", "queue"]) {
        void client.invalidateQueries({ queryKey: [key] });
      }
    },
  });
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
    if (!player.current) return;
    player.current.currentTime = ms / 1000;
    void player.current.play();
    // The detections list is below the player: bring the player into view (only scrolls
    // when it is not already visible).
    playerBox.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <div className="detail-stack">
      {s.deleted ? (
        <Empty>{t("recordings.deleted")}</Empty>
      ) : (
        <div ref={playerBox} className="detail-media">
          <VideoPlayer
            key={s.id}
            playerRef={player}
            title={t("recordings.playerTitle", { id: s.id })}
            src={`/api/segments/${s.id}/play`}
            poster={s.thumb_url}
            thumbnails={`/api/segments/${s.id}/thumbnails.vtt`}
          />
        </div>
      )}
      <div className="row wrap">
        {s.download_url && (
          <DownloadButton href={s.download_url} appearance="primary">
            {t("common.download")}
          </DownloadButton>
        )}
        {canEdit && !s.deleted && (
          <ConfirmPopover
            title={t("recordings.analyseAgainTitle")}
            body={t("recordings.analyseAgainBody")}
            confirmLabel={t("recordings.analyseAgain")}
            icon={<ArrowClockwise20Regular />}
            align="start"
            onConfirm={() => reanalyse.mutateAsync()}
          >
            <Button icon={<ArrowClockwise20Regular />} disabled={s.status === "new"}>
              {t("recordings.analyseAgain")}
            </Button>
          </ConfirmPopover>
        )}
      </div>
      <Facts>
        <Fact label={t("col.status")} className="row wrap">
          <Tag kind={s.status} title={s.error ?? undefined}>
            {stateLabel(s.status)}
          </Tag>
          {s.pinned && <Tag kind="ok">{t("recordings.keptForEvent")}</Tag>}
        </Fact>
        <Fact label={t("col.start")}>
          <Time iso={s.start_at} />
        </Fact>
        <Fact label={t("col.length")}>{duration(s.duration_s)}</Fact>
        <Fact label={t("col.size")}>{fileSize(s.size_bytes)}</Fact>
        <Fact label={t("col.light")} className="row">
          <Light night={s.is_night} long />
          {s.saturation_avg !== null && (
            <span className="muted">
              ({t("recordings.saturation", { value: decimal(s.saturation_avg, 1) })})
            </span>
          )}
        </Fact>
        <Fact label={t("recordings.sunElevation")}>
          {s.sun_elevation !== null ? `${decimal(s.sun_elevation, 1)}°` : "–"}
        </Fact>
        <Fact label={t("recordings.censorMask")}>
          {s.mask_version_id
            ? t("recordings.maskVersion", { version: s.mask_version_id })
            : t("recordings.noMask")}
        </Fact>
        <Fact label={t("recordings.storedIn")}>
          {s.archive_id !== null ? (
            <Link onClick={() => onOpenHourly(s.archive_id!)}>{t("recordings.storedHourly")}</Link>
          ) : (
            t("recordings.storedOwn")
          )}
        </Fact>
      </Facts>

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
            <ConfidenceBar value={d.confidence} compact />
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
