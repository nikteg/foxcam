// Detections → Models: compare each profile's detectors (models) on the same recordings.
import { Caption1, Select, Text } from "@fluentui/react-components";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type ModelStats } from "../api";
import { decimal, percent } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { byNumber, byString, DataTable, type Column } from "./DataTable";
import { Empty, InfoBanner, QueryState } from "./ui";

const RANGES = [
  { value: "1", label: "models.last24h" },
  { value: "7", label: "models.last7d" },
  { value: "30", label: "models.last30d" },
  { value: "all", label: "models.allTime" },
] as const;

const columns = (t: I18n["t"]): Column<ModelStats>[] => [
  {
    id: "profile",
    header: t("profiles.profile"),
    width: 130,
    cell: (r) => r.profile_name,
    compare: byString((r) => r.profile_name),
  },
  {
    id: "detector",
    header: t("col.detector"),
    width: 130,
    cell: (r) => <Text weight="semibold">{r.detector_name}</Text>,
    compare: byString((r) => r.detector_name),
  },
  {
    id: "model",
    header: t("col.model"),
    width: 260,
    minWidth: 180,
    cell: (r) => <Caption1 title={r.model ?? undefined}>{r.model ?? "–"}</Caption1>,
    compare: byString((r) => r.model),
  },
  {
    id: "detections",
    header: t("nav.detections"),
    width: 100,
    cell: (r) => r.detections,
    compare: byNumber((r) => r.detections),
  },
  {
    id: "targets",
    header: t("models.targets"),
    width: 80,
    cell: (r) => r.targets,
    compare: byNumber((r) => r.targets),
  },
  {
    id: "precision",
    header: t("models.precision"),
    width: 110,
    cell: (r) =>
      r.precision === null ? (
        "–"
      ) : (
        <span
          title={t("models.precisionHint", { good: r.feedback_correct, bad: r.feedback_wrong })}
        >
          {percent(r.precision)}{" "}
          <Caption1 className="muted">({r.feedback_correct + r.feedback_wrong})</Caption1>
        </span>
      ),
    compare: byNumber((r) => r.precision),
  },
  {
    id: "agreement",
    header: t("models.agreement"),
    width: 120,
    cell: (r) =>
      r.agreement_rate === null ? (
        "–"
      ) : (
        <span
          title={t("models.agreementHint", {
            agreed: r.segments_agreed,
            total: r.segments_with_target,
          })}
        >
          {percent(r.agreement_rate)}{" "}
          <Caption1 className="muted">({r.segments_with_target})</Caption1>
        </span>
      ),
    compare: byNumber((r) => r.agreement_rate),
  },
  {
    id: "time",
    header: t("models.avgTime"),
    width: 90,
    cell: (r) =>
      r.avg_processing_ms === null ? "–" : `${decimal(r.avg_processing_ms / 1000, 1)} s`,
    compare: byNumber((r) => r.avg_processing_ms),
  },
  {
    id: "jobs",
    header: t("models.jobs"),
    width: 230,
    cell: (r) => (
      <Caption1>
        {t("models.jobsCell", { done: r.jobs_done, dead: r.jobs_dead, skipped: r.jobs_skipped })}
      </Caption1>
    ),
    compare: byNumber((r) => r.jobs_done),
  },
];

export function ModelsSection() {
  const { t } = useI18n();
  const [{ range }, setFilter] = useFilters(["range"] as const);
  const value = range || "7";
  const stats = useQuery({
    queryKey: ["model-stats", value],
    queryFn: () => api.modelStats(value === "all" ? undefined : Number(value)),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  return (
    <>
      <InfoBanner id="models">{t("models.intro")}</InfoBanner>
      <div className="filters">
        <Select
          value={value}
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
      <QueryState error={stats.error} loading={stats.isPending} />
      {stats.data && stats.data.length > 0 && (
        <DataTable
          ariaLabel={t("models.tab")}
          items={stats.data}
          columns={columns(t)}
          getRowId={(r) => `${r.profile_id}/${r.detector_id}/${r.model ?? ""}`}
          defaultSort={{ column: "profile", direction: "ascending" }}
        />
      )}
      {stats.data?.length === 0 && <Empty>{t("models.none")}</Empty>}
    </>
  );
}
