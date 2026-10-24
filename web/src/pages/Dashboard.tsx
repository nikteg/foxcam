import {
  Caption1,
  Card,
  MessageBar,
  MessageBarBody,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import {
  Alert20Regular,
  ArrowSync20Regular,
  DismissCircle20Regular,
  CheckmarkCircle20Regular,
  Database20Regular,
  HardDrive20Regular,
  Hourglass20Regular,
  Video20Regular,
} from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { api, type QueueStats } from "../api";
import { useIsAdmin } from "../auth";

import { CameraCard } from "../components/CameraCard";
import { EventsGrid } from "../components/EventsGrid";
import { Dot, SectionLink, Empty, PageTitle, QueryState, RouterLink, Stat } from "../components/ui";
import { useI18n } from "../i18n";
import { CameraSettingsDialog } from "./Cameras";
import { decimal, duration, fileSize } from "../format";

export function Dashboard() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 10_000 });
  const events = useQuery({
    queryKey: ["events", "latest"],
    queryFn: () => api.events({ limit: 6 }),
    refetchInterval: 15_000,
  });
  const s = status.data;
  const isAdmin = useIsAdmin();
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const settingsCamera = s?.cameras.find((c) => c.id === settingsFor);
  const { t, rich } = useI18n();

  return (
    <>
      <PageTitle title={t("nav.status")} />
      <QueryState error={status.error} loading={status.isPending} />
      {s && !s.mediamtx_reachable && (
        <MessageBar intent="warning" className="block">
          <MessageBarBody>{t("dashboard.mediamtxDown")}</MessageBarBody>
        </MessageBar>
      )}

      {s && (
        <>
          <Subtitle2 as="h2" className="section first">
            {t("dashboard.overview")}
          </Subtitle2>
          <section className="stats">
            <Stat
              icon={<Video20Regular />}
              label={t("dashboard.camerasOnline")}
              value={`${s.cameras.filter((c) => c.online).length} / ${s.cameras.length}`}
            />
            <Stat icon={<Alert20Regular />} label={t("dashboard.events24h")} value={s.events_24h} />
            <Stat
              icon={<Database20Regular />}
              label={t("dashboard.recordingsStored")}
              value={fileSize(s.recordings_bytes)}
            />
            <Stat
              icon={<HardDrive20Regular />}
              label={t("dashboard.diskFree")}
              value={fileSize(s.disk_free_bytes)}
            />
          </section>
        </>
      )}

      <Subtitle2 as="h2" className="section">
        {t("nav.cameras")}
        <SectionLink to="/cameras" label={t("dashboard.manage")} />
      </Subtitle2>
      <div className="cards">
        {s?.cameras.map((c) => (
          <CameraCard
            key={c.id}
            camera={c}
            refreshKey={Math.floor(status.dataUpdatedAt / 10_000)}
            onSettings={() => setSettingsFor(c.id)}
          />
        ))}
      </div>
      {s && s.cameras.length === 0 && (
        <Empty>
          {rich("dashboard.noCameras", {
            link: <RouterLink to="/cameras">{t("dashboard.addCameraLink")}</RouterLink>,
          })}
        </Empty>
      )}

      <Subtitle2 as="h2" className="section">
        {t("dashboard.latestEvents")}
        <SectionLink to="/events" label={t("dashboard.all")} />
      </Subtitle2>
      <EventsGrid events={events.data ?? []} />

      {settingsCamera && (
        <CameraSettingsDialog
          key={settingsCamera.id}
          camera={settingsCamera}
          onClose={() => setSettingsFor(null)}
        />
      )}
      {isAdmin && s && <JobsSummary stats={s.queue} />}
    </>
  );
}

/** The detection jobs at a glance (admins): what waits and runs, the last day's work
 * per hour, and the workers. Details on the Jobs page. */
function JobsSummary({ stats: q }: { stats: QueueStats }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queue = useQuery({
    queryKey: ["queue", "summary"],
    queryFn: () => api.queue({ limit: 1 }),
    refetchInterval: 15_000,
  });
  const online = (queue.data?.workers ?? []).filter((w) => w.online);
  const avg = q.avg_processing_ms_last_hour;
  const open = (state: string) => void navigate(`/jobs?state=${state}`);
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("nav.queue")}
        <SectionLink to="/jobs" label={t("dashboard.allJobs")} />
      </Subtitle2>
      <section className="stats">
        <Stat
          icon={<Hourglass20Regular />}
          label={t("state.pending")}
          value={q.counts.pending + q.counts.failed}
          hint={
            q.oldest_pending_age_s
              ? t("dashboard.oldest", { age: duration(q.oldest_pending_age_s) })
              : undefined
          }
          onClick={() => open("pending")}
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("state.leased")}
          value={q.counts.leased}
          onClick={() => open("leased")}
        />
        <Stat
          icon={<CheckmarkCircle20Regular />}
          label={t("queue.doneLastHour")}
          value={q.done_last_hour}
          hint={avg ? t("dashboard.avgEach", { s: decimal(avg / 1000, 1) }) : undefined}
          onClick={() => open("done")}
        />
        <Stat
          icon={<DismissCircle20Regular />}
          label={t("state.dead")}
          value={q.counts.dead}
          onClick={() => open("dead")}
        />
      </section>
      <Card className="jobs-summary">
        <div className="jobs-workers">
          <Caption1 className="muted">{t("dashboard.workersOnline")}</Caption1>
          {online.length ? (
            <ul className="plain-list">
              {online.map((w) => (
                <li key={w.id} className="row">
                  <Dot ok />
                  <Text weight="semibold">{w.id}</Text>
                  <Caption1 className="muted">
                    {w.current_job_id
                      ? t("dashboard.workingOn", { id: w.current_job_id })
                      : t("dashboard.idleWorker")}
                  </Caption1>
                </li>
              ))}
            </ul>
          ) : (
            <Text className="muted">{t("dashboard.noWorkers")}</Text>
          )}
        </div>
      </Card>
    </>
  );
}
