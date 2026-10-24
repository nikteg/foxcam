import { Button } from "@fluentui/react-components";
import { ArrowDownload20Regular, Folder20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type ArchiveFile } from "../api";
import { useCameraName } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import { Empty, InfoBanner, QueryState, Tag, Time } from "../components/ui";
import { duration, fileSize, localTime } from "../format";
import { useI18n, type I18n } from "../i18n";
import { VideoPlayer } from "../components/VideoPlayer";

/** How an hourly file was made: "joined" (stream copy) or the encoder and profile. */
function useHowMade(): (a: ArchiveFile) => string {
  const { t } = useI18n();
  return (a) =>
    a.encoder === "copy" || !a.encoder
      ? t("hourly.joined")
      : `${a.encoder}${a.profile_id && a.profile_id !== "original" ? ` (${a.profile_id})` : ""}`;
}

const columns = (
  t: I18n["t"],
  cameraName: (id: string) => string,
  howMade: (a: ArchiveFile) => string,
): Column<ArchiveFile>[] => [
  {
    id: "thumb",
    header: t("col.preview"),
    width: 120,
    phone: "thumb",
    cell: (r) => r.thumb_url && <img className="mini" loading="lazy" src={r.thumb_url} alt="" />,
  },
  {
    id: "start",
    header: t("col.start"),
    width: 205,
    minWidth: 200,
    cell: (r) => <Time iso={r.start_at} />,
    compare: byString((r) => r.start_at),
  },
  {
    id: "camera",
    header: t("common.camera"),
    width: 150,
    cell: (r) => cameraName(r.camera_id),
    compare: byString((r) => cameraName(r.camera_id)),
  },
  {
    id: "length",
    header: t("col.length"),
    width: 90,
    cell: (r) => duration(r.duration_s),
    compare: byNumber((r) => r.duration_s),
  },
  {
    id: "size",
    header: t("col.size"),
    width: 90,
    cell: (r) => fileSize(r.size_bytes),
    compare: byNumber((r) => r.size_bytes),
  },
  {
    id: "segments",
    header: t("hourly.segments"),
    width: 110,
    cell: (r) =>
      r.segments_with_match > 0
        ? t("hourly.segmentsWithMatch", { n: r.segments, matches: r.segments_with_match })
        : r.segments,
    compare: byNumber((r) => r.segments),
  },
  {
    id: "how",
    header: t("hourly.how"),
    width: 200,
    cell: (r) => (r.deleted ? <Tag kind="warn">{t("hourly.deleted")}</Tag> : howMade(r)),
    compare: byString((r) => howMade(r)),
  },
];

/** Recordings → Hourly files: what compaction made of the segments. */
export function HourlyFiles({
  camera,
  day,
  selectedId,
  onSelect,
  onShowSegments,
}: {
  camera: string;
  day: string;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  /** Switch to the segments view for this file's camera and day. */
  onShowSegments: (a: ArchiveFile) => void;
}) {
  const { t } = useI18n();
  const cameraName = useCameraName();
  const howMade = useHowMade();
  const archives = useQuery({
    queryKey: ["archives", camera, day],
    // A new filter keeps the current list on screen until its own data arrives.
    placeholderData: keepPreviousData,
    queryFn: () => api.archives({ camera, day, limit: 500 }),
    refetchInterval: 30_000,
  });
  const selected = archives.data?.find((a) => a.id === selectedId);

  return (
    <>
      <InfoBanner id="archives">{t("hourly.intro")}</InfoBanner>
      <QueryState error={archives.error} loading={archives.isPending} />
      <div className="master-detail">
        <div className="master">
          {archives.data && archives.data.length > 0 && (
            <DataTable
              ariaLabel={t("hourly.title")}
              items={archives.data}
              columns={columns(t, cameraName, howMade)}
              getRowId={(r) => r.id}
              defaultSort={{ column: "start", direction: "descending" }}
              rowClassName={(r) => (r.segments_with_match > 0 ? "target" : undefined)}
              selectedId={selected?.id}
              onRowClick={(r) => onSelect(r.id)}
            />
          )}
          {archives.data?.length === 0 && <Empty>{t("hourly.none")}</Empty>}
        </div>
        <DetailPanel
          open={!!selected}
          title={
            selected ? `${cameraName(selected.camera_id)} – ${localTime(selected.start_at)}` : ""
          }
          onClose={() => onSelect(null)}
        >
          {selected && (
            <div className="detail-stack">
              {selected.play_url ? (
                <div className="detail-media">
                  <VideoPlayer
                    key={selected.id}
                    title={`${cameraName(selected.camera_id)} – ${localTime(selected.start_at)}`}
                    src={selected.play_url}
                    poster={selected.thumb_url}
                    thumbnails={`/api/archives/${selected.id}/thumbnails.vtt`}
                  />
                </div>
              ) : (
                <Empty>{t("hourly.deleted")}</Empty>
              )}
              <div className="row wrap">
                {selected.download_url && (
                  <Button
                    as="a"
                    href={selected.download_url}
                    appearance="primary"
                    icon={<ArrowDownload20Regular />}
                  >
                    {t("common.download")}
                  </Button>
                )}
                <Button icon={<Folder20Regular />} onClick={() => onShowSegments(selected)}>
                  {t("hourly.showSegments")}
                </Button>
              </div>
              <dl className="facts">
                <dt>{t("col.start")}</dt>
                <dd>
                  <Time iso={selected.start_at} />
                </dd>
                <dt>{t("col.length")}</dt>
                <dd>{duration(selected.duration_s)}</dd>
                <dt>{t("col.size")}</dt>
                <dd>{fileSize(selected.size_bytes)}</dd>
                <dt>{t("hourly.segments")}</dt>
                <dd>
                  {selected.segments_with_match > 0
                    ? t("hourly.segmentsWithMatch", {
                        n: selected.segments,
                        matches: selected.segments_with_match,
                      })
                    : selected.segments}
                </dd>
                <dt>{t("hourly.how")}</dt>
                <dd>{howMade(selected)}</dd>
              </dl>
            </div>
          )}
        </DetailPanel>
      </div>
    </>
  );
}
