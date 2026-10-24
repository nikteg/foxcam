// Jobs → Archiving: the process that joins finished periods into one archive file per
// camera (`media-streamer compact`, pipeline/compaction.py). The files themselves are
// under Recordings → Archives.
import {
  Caption1,
  MessageBar,
  MessageBarBody,
  Subtitle2,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
} from "@fluentui/react-components";
import {
  Archive20Regular,
  ArrowSync20Regular,
  Clock20Regular,
  Hourglass20Regular,
} from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";

import { api, type CompactionStatus } from "../api";
import { duration, fileSize, localTime } from "../format";
import { useI18n } from "../i18n";
import { useCameraName } from "./CameraSelect";
import { Empty, InfoBanner, QueryState, RouterLink, Stat, Tag, Time } from "./ui";

/** Running / not running / off, as a tag (also on the Status page). */
export function ArchivingState({ c }: { c: CompactionStatus }) {
  const { t } = useI18n();
  if (!c.enabled) return <Tag>{t("compaction.off")}</Tag>;
  return c.running ? (
    <Tag kind="ok">{t("compaction.running")}</Tag>
  ) : (
    <Tag kind="bad">{t("compaction.stopped")}</Tag>
  );
}

export function ArchivingSection() {
  const { t, rich } = useI18n();
  const cameraName = useCameraName();
  const status = useQuery({
    queryKey: ["archiving"],
    queryFn: api.archiving,
    refetchInterval: 10_000,
  });
  const recent = useQuery({
    queryKey: ["archives", "recent"],
    queryFn: () => api.archives({ limit: 10 }),
    refetchInterval: 30_000,
  });
  const c = status.data;
  if (!c) return <QueryState error={status.error} loading={status.isPending} />;
  const run = (r: { camera_id: string; start_at: string }) =>
    `${cameraName(r.camera_id)} ${localTime(r.start_at)}`;
  const last = c.last_built;
  const errorIsNewer = c.last_error && (!last || (c.last_error_at ?? "") > last.at);

  return (
    <>
      <InfoBanner id="archiving">{t("archiving.intro", { minutes: c.period_minutes })}</InfoBanner>
      {c.enabled && !c.running && (
        <MessageBar intent="warning" className="block">
          <MessageBarBody>
            {rich("compaction.notRunning", { command: <code>media-streamer compact</code> })}
          </MessageBarBody>
        </MessageBar>
      )}
      {errorIsNewer && (
        <MessageBar intent="error" className="block">
          <MessageBarBody>{c.last_error}</MessageBarBody>
        </MessageBar>
      )}
      <section className="stats">
        <Stat
          icon={<Clock20Regular />}
          label={t("archiving.process")}
          value={
            !c.enabled
              ? t("compaction.off")
              : c.running
                ? t("compaction.running")
                : t("compaction.stopped")
          }
          hint={
            c.heartbeat_at && (
              <>
                {t("archiving.heard")} <Time iso={c.heartbeat_at} relative />
                {c.host && ` – ${c.host}`}
              </>
            )
          }
        />
        <Stat
          icon={<ArrowSync20Regular />}
          label={t("compaction.now")}
          value={c.current ? cameraName(c.current.camera_id) : t("compaction.idle")}
          hint={
            c.current &&
            t(c.current.reencode ? "compaction.reencoding" : "compaction.joining", {
              what: localTime(c.current.start_at),
              n: c.current.segments,
            })
          }
        />
        <Stat
          icon={<Hourglass20Regular />}
          label={t("compaction.waiting")}
          value={c.waiting_periods}
          hint={t("archiving.segmentsHint", { n: c.waiting_segments })}
        />
        <Stat
          icon={<Archive20Regular />}
          label={t("archiving.files")}
          value={c.archives}
          hint={t("archiving.segmentsHint", { n: c.joined_segments })}
        />
      </section>

      <Subtitle2 as="h2" className="section">
        {t("archiving.waitingTitle")}
      </Subtitle2>
      {c.waiting_list.length ? (
        <Table className="table responsive" aria-label={t("archiving.waitingTitle")}>
          <TableHeader>
            <TableRow>
              <TableHeaderCell>{t("common.camera")}</TableHeaderCell>
              <TableHeaderCell>{t("archiving.period")}</TableHeaderCell>
              <TableHeaderCell>{t("hourly.segments")}</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {c.waiting_list.map((w) => (
              <TableRow key={`${w.camera_id}/${w.start_at}`}>
                <TableCell data-label={t("common.camera")}>{cameraName(w.camera_id)}</TableCell>
                <TableCell data-label={t("archiving.period")}>
                  <Time iso={w.start_at} />
                </TableCell>
                <TableCell data-label={t("hourly.segments")}>{w.segments}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Empty>{t("archiving.nothingWaiting")}</Empty>
      )}

      <Subtitle2 as="h2" className="section">
        {t("archiving.recentTitle")}
      </Subtitle2>
      {last && (
        <Caption1 as="p" className="muted">
          {t("compaction.last")}: {run(last)} – {fileSize(last.bytes_before)} →{" "}
          {fileSize(last.bytes_after)} (
          {last.encoder === "copy" ? t("compaction.copied") : last.encoder},{" "}
          {duration(last.seconds)})
        </Caption1>
      )}
      {recent.data && recent.data.length > 0 ? (
        <ul className="plain-list">
          {recent.data.map((a) => (
            <li key={a.id}>
              <RouterLink to={`/recordings/archives?file=${a.id}`}>
                {cameraName(a.camera_id)} {localTime(a.start_at)}
              </RouterLink>{" "}
              <Caption1 className="muted">
                {duration(a.duration_s)} – {fileSize(a.size_bytes)} –{" "}
                {a.encoder === "copy" || !a.encoder ? t("compaction.copied") : a.encoder}
              </Caption1>
            </li>
          ))}
        </ul>
      ) : (
        recent.data && <Empty>{t("hourly.none")}</Empty>
      )}
    </>
  );
}
