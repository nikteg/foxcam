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
import { ArrowClockwise16Regular, Dismiss16Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useNavigate } from "react-router";

import { api, type Job, type JobState } from "../api";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import {
  Dot,
  Empty,
  PageHeader,
  QueryState,
  Stat,
  Tag,
  Time,
  useStateLabel,
} from "../components/ui";
import { decimal, duration } from "../format";
import { WorkerAccess } from "../components/WorkerAccess";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";

const STATES: JobState[] = ["pending", "leased", "done", "failed", "dead"];

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
  const jobColumns = makeJobColumns(t, stateLabel, (id) => requeue.mutate(id), requeue.isPending);
  const q = queue.data;
  const avgMs = q?.stats.avg_processing_ms_last_hour;

  return (
    <>
      <PageHeader title={t("nav.queue")} />
      <QueryState error={queue.error} loading={queue.isPending} />
      {q && (
        <>
          <section className="stats">
            {STATES.map((st) => (
              <Stat
                key={st}
                label={stateLabel(st)}
                value={q.stats.counts[st]}
                selected={state === st}
                onClick={() => setFilter("state", state === st ? "" : st)}
              />
            ))}
            <Stat label={t("queue.oldestWaiting")} value={duration(q.stats.oldest_pending_age_s)} />
            <Stat label={t("queue.doneLastHour")} value={q.stats.done_last_hour} />
            <Stat
              label={t("queue.avgProcessing")}
              value={avgMs ? `${decimal(avgMs / 1000, 1)} s` : "–"}
            />
          </section>

          <Subtitle2 as="h2" className="section">
            {t("queue.workers")}
          </Subtitle2>
          <Table className="table responsive" aria-label={t("queue.workers")}>
            <TableHeader>
              <TableRow>
                <TableHeaderCell>{t("col.worker")}</TableHeaderCell>
                <TableHeaderCell>{t("queue.info")}</TableHeaderCell>
                <TableHeaderCell>{t("queue.lastSeen")}</TableHeaderCell>
                <TableHeaderCell>{t("queue.currentJob")}</TableHeaderCell>
                <TableHeaderCell>{t("queue.doneCount")}</TableHeaderCell>
                <TableHeaderCell>{t("queue.failedCount")}</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.workers.map((w) => (
                <TableRow key={w.id}>
                  <TableCell data-label={t("col.worker")}>
                    <span className="row">
                      <Dot ok={w.online} />
                      <Text weight="semibold" wrap={false}>
                        {w.id}
                      </Text>
                    </span>
                  </TableCell>
                  <TableCell data-label={t("queue.info")} className="full mono">
                    {/* One chip per key so values wrap as units, never mid-word. */}
                    <span className="chips">
                      {Object.entries(w.info).map(([k, v]) => (
                        <span key={k}>
                          <span className="muted">{k}=</span>
                          {String(v)}
                        </span>
                      ))}
                    </span>
                  </TableCell>
                  <TableCell data-label={t("queue.lastSeen")}>
                    <Time iso={w.last_seen} relative />
                  </TableCell>
                  <TableCell data-label={t("queue.currentJob")}>
                    {w.current_job_id ?? "–"}
                  </TableCell>
                  <TableCell data-label={t("queue.doneCount")}>{w.jobs_done}</TableCell>
                  <TableCell data-label={t("queue.failedCount")}>{w.jobs_failed}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {q.workers.length === 0 && <Empty>{t("queue.noWorkers")}</Empty>}

          <WorkerAccess onlineIds={new Set(q.workers.filter((w) => w.online).map((w) => w.id))} />

          <Subtitle2 as="h2" className="section row">
            {t("queue.jobs")}
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
  );
}

function makeJobColumns(
  t: I18n["t"],
  stateLabel: (s: string) => string,
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
      width: 100,
      cell: (j) => j.camera_id,
      compare: byString((j) => j.camera_id),
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
