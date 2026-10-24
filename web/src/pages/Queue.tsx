import {
  Button,
  Caption1,
  Subtitle2,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from "@fluentui/react-components";
import {
  Add20Regular,
  ArrowClockwise16Regular,
  ArrowSync20Regular,
  ArrowTrending20Regular,
  CheckmarkCircle20Regular,
  Dismiss16Regular,
  DismissCircle20Regular,
  ErrorCircle20Regular,
  Gauge20Regular,
  Hourglass20Regular,
  Timer20Regular,
} from "@fluentui/react-icons";
import type { ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useState } from "react";
import { useNavigate } from "react-router";

import { api, type Job, type JobState } from "../api";
import { useCameraName } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import {
  Empty,
  PageHeader,
  PageTabs,
  QueryState,
  Stat,
  Tag,
  Time,
  useStateLabel,
  useSubPage,
} from "../components/ui";
import { decimal, duration } from "../format";
import { useProfileName, useProfiles } from "../components/ProfileSelect";
import { AddWorkerDialog } from "../components/AddWorkerDialog";
import { WorkersSection } from "../components/WorkersSection";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";

const STATES: JobState[] = ["pending", "leased", "done", "failed", "dead"];

const STATE_ICON: Record<JobState, ReactElement> = {
  pending: <Hourglass20Regular />,
  leased: <ArrowSync20Regular />,
  done: <CheckmarkCircle20Regular />,
  failed: <ErrorCircle20Regular />,
  dead: <DismissCircle20Regular />,
};

export function Queue() {
  const [{ state }, setFilter] = useFilters(["state"] as const);
  const client = useQueryClient();
  const queue = useQuery({
    queryKey: ["queue", state],
    queryFn: () => api.queue({ state, limit: 100 }),
    refetchInterval: 5_000,
  });
  const requeue = useMutation({
    mutationFn: api.requeue,
    onSuccess: () => client.invalidateQueries({ queryKey: ["queue"] }),
  });
  const navigate = useNavigate();
  const { t } = useI18n();
  const stateLabel = useStateLabel();
  const cameraName = useCameraName();
  const profileName = useProfileName();
  const profiles = useProfiles();
  const jobColumns = makeJobColumns(
    t,
    stateLabel,
    profileName,
    cameraName,
    (id) => requeue.mutate(id),
    requeue.isPending,
  );
  const q = queue.data;
  const avgMs = q?.stats.avg_processing_ms_last_hour;
  const tab = useSubPage("/jobs", ["queue", "workers"] as const);
  const [addingWorker, setAddingWorker] = useState(false);

  return (
    <>
      <PageHeader
        title={t("nav.queue")}
        actions={
          <Button
            appearance="primary"
            icon={<Add20Regular />}
            onClick={() => setAddingWorker(true)}
          >
            {t("workers.add")}
          </Button>
        }
      />
      <AddWorkerDialog open={addingWorker} onClose={() => setAddingWorker(false)} />
      <QueryState error={queue.error} loading={queue.isPending} />
      {q && (
        <>
          <PageTabs
            base="/jobs"
            current={tab}
            tabs={[
              { id: "queue", label: t("queue.queueTab") },
              { id: "workers", label: t("queue.workers") },
            ]}
          />

          {tab === "workers" && <WorkersSection workers={q.workers} />}

          {tab === "queue" && (
            <>
              <section className="stats">
                {STATES.map((st) => (
                  <Stat
                    key={st}
                    icon={STATE_ICON[st]}
                    label={stateLabel(st)}
                    value={q.stats.counts[st]}
                    selected={state === st}
                    // The counters filter the job list, so they bring you to it.
                    onClick={() => setFilter("state", state === st ? "" : st)}
                  />
                ))}
                <Stat
                  icon={<Timer20Regular />}
                  label={t("queue.oldestWaiting")}
                  value={duration(q.stats.oldest_pending_age_s)}
                />
                <Stat
                  icon={<ArrowTrending20Regular />}
                  label={t("queue.doneLastHour")}
                  value={q.stats.done_last_hour}
                />
                <Stat
                  icon={<Gauge20Regular />}
                  label={t("queue.avgProcessing")}
                  value={avgMs ? `${decimal(avgMs / 1000, 1)} s` : "–"}
                />
              </section>
              {profiles.data && profiles.data.length > 1 && (
                <>
                  <Subtitle2 as="h2" className="section">
                    {t("queue.byProfile")}
                  </Subtitle2>
                  <Table className="table responsive" aria-label={t("queue.byProfile")}>
                    <TableHeader>
                      <TableRow>
                        <TableHeaderCell>{t("profiles.profile")}</TableHeaderCell>
                        <TableHeaderCell>{t("profiles.waiting")}</TableHeaderCell>
                        <TableHeaderCell>{t("queue.running")}</TableHeaderCell>
                        <TableHeaderCell>{t("profiles.workers")}</TableHeaderCell>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {profiles.data.map((p) => (
                        <TableRow key={p.id}>
                          <TableCell data-label={t("profiles.profile")}>
                            <Text weight="semibold">{p.display_name}</Text>
                          </TableCell>
                          <TableCell data-label={t("profiles.waiting")}>{p.waiting_jobs}</TableCell>
                          <TableCell data-label={t("queue.running")}>
                            {q.stats.by_profile[p.id]?.leased ?? 0}
                          </TableCell>
                          <TableCell data-label={t("profiles.workers")}>
                            {p.workers_online.length ? (
                              p.workers_online.join(", ")
                            ) : p.waiting_jobs > 0 ? (
                              <Tag kind="warn" title={t("profiles.stuck")}>
                                {t("profiles.noWorkers")}
                              </Tag>
                            ) : (
                              "–"
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </>
              )}

              <Subtitle2 as="h2" className="section row">
                {t("queue.latestJobs")}
                {state && (
                  <>
                    <Tag kind={state}>{stateLabel(state)}</Tag>
                    <Button
                      size="small"
                      appearance="subtle"
                      icon={<Dismiss16Regular />}
                      onClick={() => setFilter("state", "")}
                    >
                      {t("queue.clearFilter")}
                    </Button>
                  </>
                )}
              </Subtitle2>
              {q.jobs.length > 0 && (
                <DataTable
                  ariaLabel="Jobs"
                  items={q.jobs}
                  columns={jobColumns}
                  getRowId={(j) => j.id}
                  defaultSort={{ column: "id", direction: "descending" }}
                  onRowClick={(j) => navigate(`/recordings?selected=${j.segment_id}`)}
                />
              )}
              {q.jobs.length === 0 && <Empty>{t("queue.noJobs")}</Empty>}
            </>
          )}
        </>
      )}
    </>
  );
}

function makeJobColumns(
  t: I18n["t"],
  stateLabel: (s: string) => string,
  profileName: (id: string | null) => string,
  cameraName: (id: string) => string,
  retry: (id: number) => void,
  busy: boolean,
): Column<Job>[] {
  return [
    {
      id: "id",
      header: t("col.job"),
      width: 85,
      cell: (j) => `#${j.id}`,
      compare: byNumber((j) => j.id),
    },
    {
      id: "camera",
      header: t("common.camera"),
      width: 150,
      cell: (j) => cameraName(j.camera_id),
      compare: byString((j) => cameraName(j.camera_id)),
    },
    {
      id: "profile",
      header: t("profiles.profile"),
      width: 110,
      cell: (j) => profileName(j.profile_id),
      compare: byString((j) => j.profile_id),
    },
    {
      id: "segment",
      header: t("col.segmentStart"),
      width: 205,
      minWidth: 200,
      cell: (j) => <Time iso={j.segment_start} />,
      compare: byString((j) => j.segment_start),
    },
    {
      id: "state",
      header: t("col.state"),
      width: 100,
      cell: (j) => <Tag kind={j.state}>{stateLabel(j.state)}</Tag>,
      compare: byString((j) => j.state),
    },
    {
      id: "attempts",
      header: t("col.attempts"),
      width: 90,
      cell: (j) => j.attempts,
      compare: byNumber((j) => j.attempts),
    },
    {
      id: "worker",
      header: t("col.worker"),
      width: 160,
      cell: (j) => <span title={j.leased_by ?? undefined}>{j.leased_by ?? "–"}</span>,
      compare: byString((j) => j.leased_by),
    },
    {
      id: "model",
      header: t("col.model"),
      width: 130,
      cell: (j) => <Caption1>{j.model ?? "–"}</Caption1>,
    },
    {
      id: "took",
      header: t("col.took"),
      width: 80,
      cell: (j) => (j.processing_ms ? `${(j.processing_ms / 1000).toFixed(1)}s` : "–"),
      compare: byNumber((j) => j.processing_ms),
    },
    {
      id: "error",
      header: t("col.error"),
      width: 220,
      phone: "full",
      cell: (j) => (
        <Caption1 className="err" title={j.error ?? undefined}>
          {j.error?.slice(0, 80)}
        </Caption1>
      ),
    },
    {
      id: "actions",
      header: t("col.actions"),
      width: 150,
      phone: "nolabel",
      cell: (j) =>
        j.state !== "pending" &&
        j.state !== "leased" && (
          <Button
            size="small"
            icon={<ArrowClockwise16Regular />}
            disabled={busy}
            onClick={() => retry(j.id)}
          >
            {t("queue.retry")}
          </Button>
        ),
    },
  ];
}
