import { MessageBar, MessageBarBody, Subtitle2 } from "@fluentui/react-components";
import {
  Alert20Regular,
  Archive20Regular,
  Clock20Regular,
  ArrowSync20Regular,
  DismissCircle20Regular,
  CheckmarkCircle20Regular,
  Database20Regular,
  HardDrive20Regular,
  Hourglass20Regular,
  PlugConnected20Regular,
  Video20Regular,
} from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { api, type CompactionStatus, type PreprocessingStatus, type Status } from "../api";
import { useIsAdmin } from "../auth";

import { CameraCard } from "../components/CameraCard";
import { useCameraName } from "../components/CameraSelect";
import { EventsGrid } from "../components/EventsGrid";
import { InstallHint } from "../components/InstallHint";
import { LiveDrawer } from "../components/LiveDrawer";
import { SectionLink, Empty, PageTitle, QueryState, RouterLink, Stat } from "../components/ui";
import { useI18n } from "../i18n";
import { CameraSettingsDialog } from "./Cameras";
import { decimal, duration, fileSize } from "../format";
import { CardsSkeleton, StatsSkeleton } from "../components/Skeletons";

export function Dashboard() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 10_000 });
  const events = useQuery({
    queryKey: ["events", "latest"],
    // Marked wrong in the detection drawer: not worth a place on the start page.
    queryFn: () => api.events({ limit: 6, hide_wrong: true }),
    refetchInterval: 15_000,
  });
  const s = status.data;
  const isAdmin = useIsAdmin();
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const [liveFor, setLiveFor] = useState<string | null>(null);
  const settingsCamera = s?.cameras.find((c) => c.id === settingsFor);
  const { t, rich } = useI18n();
  const navigate = useNavigate();

  return (
    <>
      <PageTitle title={t("nav.status")} />
      <InstallHint />
      {s && !s.mediamtx_reachable && (
        <MessageBar intent="warning" className="block">
          <MessageBarBody>{t("dashboard.mediamtxDown")}</MessageBarBody>
        </MessageBar>
      )}

      <Subtitle2 as="h2" className="section first">
        {t("dashboard.overview")}
      </Subtitle2>
      <QueryState error={status.error} loading={status.isPending} skeleton={<StatsSkeleton />} />
      {s && (
        <>
          <section className="stats">
            <Stat
              icon={<Video20Regular />}
              label={t("dashboard.camerasOnline")}
              value={`${s.cameras.filter((c) => c.online).length} / ${s.cameras.length}`}
              onClick={() => void navigate("/cameras")}
            />
            <Stat
              icon={<Alert20Regular />}
              label={t("dashboard.events24h")}
              value={s.events_24h}
              onClick={() => void navigate("/events")}
            />
            <Stat
              icon={<Database20Regular />}
              label={t("dashboard.recordingsStored")}
              value={fileSize(s.recordings_bytes)}
              onClick={() => void navigate("/recordings")}
            />
            <Stat
              icon={<HardDrive20Regular />}
              label={t("dashboard.diskFree")}
              value={fileSize(s.disk_free_bytes)}
              // How long recordings are kept and the disk cap (admins).
              onClick={isAdmin ? () => void navigate("/settings/retention") : undefined}
            />
          </section>
        </>
      )}

      <Subtitle2 as="h2" className="section">
        {t("nav.cameras")}
        <SectionLink to="/cameras" label={t("dashboard.manage")} />
      </Subtitle2>
      {status.isPending && <CardsSkeleton count={2} />}
      <div className="cards">
        {s?.cameras.map((c) => (
          <CameraCard
            key={c.id}
            camera={c}
            onLive={() => setLiveFor(c.id)}
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
      <QueryState error={events.error} loading={events.isPending} skeleton={<CardsSkeleton />} />
      {events.data && <EventsGrid events={events.data} />}

      {liveFor && s && (
        <LiveDrawer cameras={s.cameras} cameraId={liveFor} onClose={() => setLiveFor(null)} />
      )}
      {settingsCamera && (
        <CameraSettingsDialog
          key={settingsCamera.id}
          camera={settingsCamera}
          onClose={() => setSettingsFor(null)}
        />
      )}
      {/* Skeletons with their headings while the status loads. */}
      {isAdmin && (s || status.isPending) && <DetectionSummary status={s} />}
      {isAdmin && (s?.preprocessing || status.isPending) && (
        <PreparationSummary prep={s?.preprocessing ?? undefined} />
      )}
      {isAdmin && (s?.compaction || status.isPending) && (
        <ArchivingSummary archiving={s?.compaction ?? undefined} />
      )}
    </>
  );
}

/** Detection at a glance (admins): what waits and runs, the last hour's work, and the
 * workers. Details on Jobs → Detection and Detection workers. */
function DetectionSummary({ status: s }: { status?: Status }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const heading = (
    <Subtitle2 as="h2" className="section">
      {t("queue.queueTab")}
      <SectionLink to="/jobs/detection" label={t("dashboard.allJobs")} />
    </Subtitle2>
  );
  if (!s) {
    return (
      <>
        {heading}
        <StatsSkeleton count={5} />
      </>
    );
  }
  const q = s.queue;
  const w = s.workers;
  const avg = q.avg_processing_ms_last_hour;
  const open = (state: string) => void navigate(`/jobs/detection?state=${state}`);
  return (
    <>
      {heading}
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
        {w && (
          <Stat
            icon={<PlugConnected20Regular />}
            label={t("dashboard.workersOnline")}
            value={`${w.online} / ${w.total}`}
            hint={w.online ? t("dashboard.workersBusy", { n: w.busy }) : undefined}
            onClick={() => void navigate("/jobs/detection-workers")}
          />
        )}
      </section>
    </>
  );
}

/** The server preparing recordings before they get detection jobs (admins), like
 * Jobs → Preparation. */
function PreparationSummary({ prep: p }: { prep?: PreprocessingStatus }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const heading = (
    <Subtitle2 as="h2" className="section">
      {t("prep.tab")}
      <SectionLink to="/jobs/preparation" label={t("prep.tab")} />
    </Subtitle2>
  );
  if (!p) {
    return (
      <>
        {heading}
        <StatsSkeleton />
      </>
    );
  }
  const open = () => void navigate("/jobs/preparation");
  return (
    <>
      {heading}
      <section className="stats">
        <Stat
          icon={<Hourglass20Regular />}
          label={t("prep.waiting")}
          value={p.waiting}
          hint={
            p.oldest_waiting_age_s
              ? t("dashboard.oldest", { age: duration(p.oldest_waiting_age_s) })
              : undefined
          }
          onClick={open}
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("prep.preparing")}
          value={`${p.preparing} / ${p.concurrency}`}
          onClick={open}
        />
        <Stat
          icon={<CheckmarkCircle20Regular />}
          label={t("queue.doneLastHour")}
          value={p.done_last_hour}
          hint={
            p.avg_seconds_last_hour != null
              ? t("dashboard.avgEach", { s: decimal(p.avg_seconds_last_hour, 1) })
              : undefined
          }
          onClick={open}
        />
        <Stat
          icon={<DismissCircle20Regular />}
          label={t("queue.failedCount")}
          value={p.failed}
          onClick={open}
        />
      </section>
    </>
  );
}

/** The compact process joining finished periods into archive files (admins), like
 * Jobs → Archiving. */
function ArchivingSummary({ archiving: a }: { archiving?: CompactionStatus }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const cameraName = useCameraName();
  const heading = (
    <Subtitle2 as="h2" className="section">
      {t("compaction.title")}
      <SectionLink to="/jobs/archiving" label={t("compaction.title")} />
    </Subtitle2>
  );
  if (!a) {
    return (
      <>
        {heading}
        <StatsSkeleton />
      </>
    );
  }
  const open = () => void navigate("/jobs/archiving");
  return (
    <>
      {heading}
      <section className="stats">
        <Stat
          icon={<Clock20Regular />}
          label={t("archiving.process")}
          value={
            !a.enabled
              ? t("compaction.off")
              : a.running
                ? t("compaction.running")
                : t("compaction.stopped")
          }
          onClick={open}
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("compaction.now")}
          value={a.current ? cameraName(a.current.camera_id) : t("compaction.idle")}
          onClick={open}
        />
        <Stat
          icon={<Hourglass20Regular />}
          label={t("compaction.waiting")}
          value={a.waiting_periods}
          hint={t("archiving.segmentsHint", { n: a.waiting_segments })}
          onClick={open}
        />
        <Stat
          icon={<Archive20Regular />}
          label={t("archiving.files")}
          value={a.archives}
          onClick={open}
        />
      </section>
    </>
  );
}
