// Events → Motion: what the two motion options of a profile would do with the recordings
// and targets so far (shadow mode, server: pipeline/motion_report.py), split by the
// correct / wrong marks, so they can be turned on where they help.
import {
  Button,
  Caption1,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Select,
  Spinner,
} from "@fluentui/react-components";
import { Run20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type MotionReportRow, type Rated } from "../api";
import { percent } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { CameraTag, useCameraName } from "./CameraSelect";
import { byNumber, byString, DataTable, type Column } from "./DataTable";
import { ProfileTag } from "./ProfileSelect";
import { Empty, InfoBanner, QueryState, Tag } from "./ui";

const RANGES = [
  { value: "1", label: "models.last24h" },
  { value: "7", label: "models.last7d" },
  { value: "30", label: "models.last30d" },
] as const;

const total = (r: Rated) => r.correct + r.wrong + r.unrated;

/** Verdict for "Skip recordings without motion". */
function skipVerdict(t: I18n["t"], r: MotionReportRow) {
  if (!r.measured) return null;
  const saves = percent(r.without_motion / r.measured);
  if (r.in_still.correct)
    return <Tag kind="bad">{t("motionReport.misses", { n: r.in_still.correct })}</Tag>;
  if (!r.without_motion) return <Tag>{t("motionReport.nothingToSkip")}</Tag>;
  if (r.in_still.unrated)
    return <Tag kind="warn">{t("motionReport.checkUnrated", { n: r.in_still.unrated })}</Tag>;
  return <Tag kind="ok">{t("motionReport.saves", { value: saves })}</Tag>;
}

/** Verdict for "Ignore standing still". */
function requireVerdict(t: I18n["t"], r: MotionReportRow) {
  if (!total(r.targets)) return null;
  const d = r.standing_still;
  if (d.correct) return <Tag kind="bad">{t("motionReport.dropsCorrect", { n: d.correct })}</Tag>;
  if (d.wrong) return <Tag kind="ok">{t("motionReport.dropsWrong", { n: d.wrong })}</Tag>;
  if (d.unrated) return <Tag kind="warn">{t("motionReport.dropsUnrated", { n: d.unrated })}</Tag>;
  return <Tag>{t("motionReport.changesNothing")}</Tag>;
}

const columns = (t: I18n["t"], cameraName: (id: string) => string): Column<MotionReportRow>[] => [
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (r) => <CameraTag id={r.camera_id} />,
    compare: byString((r) => cameraName(r.camera_id)),
  },
  {
    id: "profile",
    header: t("profiles.profile"),
    width: 150,
    cell: (r) => <ProfileTag id={r.profile_id} />,
    compare: byString((r) => r.profile_name),
  },
  {
    id: "recordings",
    header: t("motionReport.recordings"),
    width: 120,
    cell: (r) => (
      <span title={t("motionReport.measuredHint")}>
        {r.measured} <Caption1 className="muted">/ {r.recordings}</Caption1>
      </span>
    ),
    compare: byNumber((r) => r.measured),
  },
  {
    id: "skip",
    header: t("profiles.skipWithoutMotion"),
    width: 260,
    minWidth: 200,
    phone: "full",
    cell: (r) =>
      r.measured ? (
        <span className="report-cell">
          <span className="row wrap">
            {skipVerdict(t, r)}
            {r.skip_without_motion && <Tag kind="target">{t("motionReport.on")}</Tag>}
          </span>
          <Caption1 className="muted">
            {t("motionReport.skipDetail", {
              still: r.without_motion,
              measured: r.measured,
              correct: r.in_still.correct,
              wrong: r.in_still.wrong,
              unrated: r.in_still.unrated,
            })}
          </Caption1>
        </span>
      ) : (
        <Caption1 className="muted">{t("motionReport.notMeasured")}</Caption1>
      ),
    compare: byNumber((r) => (r.measured ? r.without_motion / r.measured : -1)),
  },
  {
    id: "require",
    header: t("motionReport.stillHeader"),
    width: 260,
    minWidth: 200,
    phone: "full",
    cell: (r) =>
      total(r.targets) ? (
        <span className="report-cell">
          <span className="row wrap">
            {requireVerdict(t, r)}
            {r.still_minutes !== null ? (
              <Tag kind="target">{t("motionReport.on")}</Tag>
            ) : (
              <Caption1 className="muted">
                {t("motionReport.tried", { n: r.still_minutes_tried })}
              </Caption1>
            )}
          </span>
          <Caption1 className="muted">
            {t("motionReport.stillDetail", {
              minutes: r.still_minutes_tried,
              correct: r.standing_still.correct,
              wrong: r.standing_still.wrong,
              unrated: r.standing_still.unrated,
              total: total(r.targets),
            })}
          </Caption1>
        </span>
      ) : (
        <Caption1 className="muted">{t("motionReport.noTargets")}</Caption1>
      ),
    compare: byNumber((r) => total(r.standing_still)),
  },
];

export function MotionSection() {
  const { t } = useI18n();
  const cameraName = useCameraName();
  const client = useQueryClient();
  const [{ range }, setFilter] = useFilters(["range"] as const);
  const days = Number(range || "7");
  const report = useQuery({
    queryKey: ["motion-report", days],
    queryFn: () => api.motionReport(days),
    placeholderData: keepPreviousData,
    // Follow the backfill while it runs.
    refetchInterval: (q) => (q.state.data?.backfill.running ? 3_000 : 60_000),
  });
  const backfill = useMutation({
    mutationFn: () => api.motionBackfill(days),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["motion-report"] }),
  });
  const data = report.data;
  const running = data?.backfill.running;

  return (
    <>
      <InfoBanner id="motion-report">{t("motionReport.intro")}</InfoBanner>
      <div className="filters">
        <Select
          value={String(days)}
          aria-label={t("models.range")}
          onChange={(_, d) => setFilter("range", d.value === "7" ? "" : d.value)}
        >
          {RANGES.map((r) => (
            <option key={r.value} value={r.value}>
              {t(r.label)}
            </option>
          ))}
        </Select>
      </div>
      {running ? (
        <MessageBar className="block">
          <MessageBarBody className="row">
            <Spinner size="tiny" />
            {t("motionReport.measuring", { done: data.backfill.done, total: data.backfill.total })}
          </MessageBarBody>
        </MessageBar>
      ) : (
        !!data?.unmeasured && (
          <MessageBar className="block">
            <MessageBarBody>{t("motionReport.unmeasured", { n: data.unmeasured })}</MessageBarBody>
            <MessageBarActions>
              <Button
                size="small"
                icon={<Run20Regular />}
                disabled={backfill.isPending}
                onClick={() => backfill.mutate()}
              >
                {t("motionReport.measure")}
              </Button>
            </MessageBarActions>
          </MessageBar>
        )
      )}
      <QueryState error={report.error ?? backfill.error} loading={report.isPending} />
      {data && data.rows.length > 0 && (
        <DataTable
          ariaLabel={t("motion.title")}
          items={data.rows}
          columns={columns(t, cameraName)}
          getRowId={(r) => `${r.camera_id}/${r.profile_id}`}
          defaultSort={{ column: "camera", direction: "ascending" }}
        />
      )}
      {data?.rows.length === 0 && <Empty>{t("motionReport.none")}</Empty>}
    </>
  );
}
