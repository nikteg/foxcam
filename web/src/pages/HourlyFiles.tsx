import { Button } from "@fluentui/react-components";
import { Folder20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type ArchiveFile } from "../api";
import { useCameraName } from "../components/CameraSelect";
import { byNumber, byString, DataTable, type Column } from "../components/DataTable";
import { DetailPanel } from "../components/DetailPanel";
import {
  DownloadButton,
  Empty,
  Fact,
  Facts,
  InfoBanner,
  QueryState,
  Tag,
  Time,
} from "../components/ui";
import { duration, fileSize, localTime } from "../format";
import { useI18n, type I18n } from "../i18n";
import { VideoPlayer } from "../components/VideoPlayer";

/** Re-encoded when joined (a camera's recording profile) or kept as the camera sent it. */
function useHowMade(): (a: ArchiveFile) => string {
  const { t } = useI18n();
  return (a) =>
    a.encoder === "copy" || !a.encoder
      ? t("hourly.notReencoded")
      : t("hourly.reencodedWith", {
          encoder: a.encoder,
          profile: a.profile_id && a.profile_id !== "original" ? ` (${a.profile_id})` : "",
        });
}

const columns = (t: I18n["t"], cameraName: (id: string) => string): Column<ArchiveFile>[] => [
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
    // Retention removed the file: say so instead of its old size.
    cell: (r) =>
      r.deleted ? <Tag kind="warn">{t("hourly.deleted")}</Tag> : fileSize(r.size_bytes),
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
              columns={columns(t, cameraName)}
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
                  <DownloadButton href={selected.download_url} appearance="primary">
                    {t("common.download")}
                  </DownloadButton>
                )}
                <Button icon={<Folder20Regular />} onClick={() => onShowSegments(selected)}>
                  {t("hourly.showSegments")}
                </Button>
              </div>
              <Facts>
                <Fact label={t("col.start")}>
                  <Time iso={selected.start_at} />
                </Fact>
                <Fact label={t("col.length")}>{duration(selected.duration_s)}</Fact>
                <Fact label={t("col.size")}>{fileSize(selected.size_bytes)}</Fact>
                <Fact label={t("hourly.segments")}>
                  {selected.segments_with_match > 0
                    ? t("hourly.segmentsWithMatch", {
                        n: selected.segments,
                        matches: selected.segments_with_match,
                      })
                    : selected.segments}
                </Fact>
                <Fact label={t("hourly.reencoded")}>{howMade(selected)}</Fact>
              </Facts>
            </div>
          )}
        </DetailPanel>
      </div>
    </>
  );
}
