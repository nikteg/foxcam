import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  CardPreview,
  Text,
} from "@fluentui/react-components";
import { Dismiss24Regular, Info20Regular, Play20Filled } from "@fluentui/react-icons";
import { Fragment, useState } from "react";
import { useSearchParams } from "react-router";

import { api, type DetectionEvent } from "../api";
import { duration, localDay, localTime } from "../format";
import { useI18n } from "../i18n";
import { useCameraName } from "./CameraSelect";
import { withShift, type Selection } from "./Bulk";
import { DetailPanel } from "./DetailPanel";
import { EventDetail } from "./EventDetail";
import { FavoriteStar } from "./Favorite";
import { EventFeedback } from "./Feedback";
import { useVideoTimeline } from "./Motion";
import {
  useDetectorNames,
  useProfileName,
  useProfiles,
  useSeveralDetectors,
} from "./ProfileSelect";
import {
  Checkbox,
  ConfidenceBar,
  DownloadButton,
  DrawerTitle,
  Empty,
  Light,
  Tag,
  Time,
} from "./ui";
import { VideoPlayer } from "./VideoPlayer";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";
import { eventThumbBox } from "./Boxes";
import { FrameImage } from "./FrameImage";
import { useLinked } from "../useLinked";

export function EventsGrid({
  events,
  byDay = false,
  selection,
}: {
  events: DetectionEvent[];
  /** A heading per day (the Events page; the dashboard shows only the latest few). */
  byDay?: boolean;
  /** A checkbox on each card for bulk actions (components/Bulk). */
  selection?: Selection<DetectionEvent>;
}) {
  const { t } = useI18n();
  const [playing, setPlaying] = useState<DetectionEvent | null>(null);
  const playingTimeline = useVideoTimeline("events", playing?.clip_url ? playing.id : null);
  // The open event's details are in the URL (?details=<id>) on whichever page shows the
  // grid, so the nav tab stays put, Back closes it and it can be linked. Looked up in the
  // (refreshed) list, so marking it right or wrong shows at once.
  const [params, setParams] = useSearchParams();
  const open = params.get("details");
  const found = useLinked(events, Number(open) || null, "events", api.event) ?? null;
  // The last version seen in the list: an event can leave it while open (marked wrong on
  // the Status page, which hides those), and the drawer stays until it is closed.
  const [seen, setSeen] = useState<DetectionEvent | null>(null);
  if (found && found !== seen) setSeen(found);
  const details = found ?? (seen && String(seen.id) === open ? seen : null);
  const setDetails = (e: DetectionEvent | null) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (e) next.set("details", String(e.id));
        else next.delete("details");
        return next;
      },
      // Opening adds a history entry (Back closes it); closing does not.
      { replace: !e },
    );
  const profileName = useProfileName();
  const cameraName = useCameraName();
  const manyProfiles = (useProfiles().data?.length ?? 0) > 1;
  const severalDetectors = useSeveralDetectors();
  const detectorNames = useDetectorNames();
  return (
    <>
      {events.length === 0 && <Empty>{t("events.none")}</Empty>}
      {/* While some are picked, every card shows its checkbox (not only on hover). */}
      {/* The drawer below still opens with none (a link to one the filters leave out). */}
      <div
        className={`cards ${selection?.chosen.length ? "picking" : ""}`}
        hidden={events.length === 0}
      >
        {events.map((e, i) => (
          <Fragment key={e.id}>
            {byDay && (i === 0 || localDay(events[i - 1]!.first_ts) !== localDay(e.first_ts)) && (
              <h2 className="cards-day">{localDay(e.first_ts)}</h2>
            )}
            <Card className={`event-card ${selection?.isSelected(e) ? "checked" : ""}`}>
              <CardPreview>
                {selection?.canSelect(e) && (
                  <span className="event-pick">
                    <Checkbox
                      checked={selection.isSelected(e)}
                      aria-label={t("bulk.selectThis", { label: e.label })}
                      onChange={(ev) => selection.toggle(e, withShift(ev), events)}
                    />
                  </span>
                )}
                {e.thumb_url ? (
                  <button
                    type="button"
                    className="thumb-button"
                    onClick={() => setDetails(e)}
                    aria-label={t("events.openDetails", { label: e.label })}
                    title={t("events.showDetectionsHint")}
                  >
                    <FrameImage
                      className="thumb"
                      lazy
                      src={e.thumb_url}
                      boxes={eventThumbBox(e)}
                      alt={t("events.thumbAlt", {
                        label: e.label,
                        camera: cameraName(e.camera_id),
                      })}
                    />
                    <span className="thumb-overlay" aria-hidden="true">
                      <Info20Regular />
                    </span>
                  </button>
                ) : (
                  <div className="thumb placeholder">
                    {e.building ? t("events.buildingClip") : t("events.noThumbnail")}
                  </div>
                )}
              </CardPreview>
              <CardHeader
                header={
                  <span className="row wrap">
                    {/* The details drawer: frame, box, feedback. */}
                    <button
                      type="button"
                      className="event-title"
                      onClick={() => setDetails(e)}
                      title={t("events.showDetectionsHint")}
                    >
                      <Text weight="semibold">{e.label}</Text>
                    </button>
                    <ConfidenceBar value={e.max_confidence} compact />
                    <Light night={e.is_night} />
                    {e.feedback === "wrong" && (
                      <Tag kind="bad" title={t("events.markedWrongHint")}>
                        {t("events.markedWrong")}
                      </Tag>
                    )}
                    {!e.confirmed && (
                      <Tag kind="pending" title={t("events.unconfirmedHint")}>
                        {t("events.unconfirmed")}
                      </Tag>
                    )}
                    <Caption1 className="muted">{cameraName(e.camera_id)}</Caption1>
                  </span>
                }
                description={
                  <Caption1 className="muted">
                    {severalDetectors && e.detectors.length > 0 && (
                      <span className="chips event-models" title={t("events.seenByModels")}>
                        {e.detectors.map((d) => (
                          <span key={d}>{detectorNames.get(d) ?? d}</span>
                        ))}
                      </span>
                    )}
                    {manyProfiles && <>{profileName(e.profile_id)} – </>}
                    <Time iso={e.first_ts} /> –{" "}
                    {t("events.detectionCount", { count: e.detection_count })} –{" "}
                    {duration(e.clip_duration_s)}
                  </Caption1>
                }
              />
              <CardFooter className="wrap">
                {e.clip_url ? (
                  <>
                    <Button size="small" icon={<Play20Filled />} onClick={() => setPlaying(e)}>
                      {t("common.play")}
                    </Button>
                    <DownloadButton size="small" href={`${e.clip_url}?download=true`}>
                      {t("common.download")}
                    </DownloadButton>
                  </>
                ) : e.clip_error ? (
                  <Tag kind="bad" title={e.clip_error}>
                    {t("events.clipFailed")}
                  </Tag>
                ) : (
                  <Tag kind="pending">{t("events.buildingClip")}</Tag>
                )}
                <span className="spacer" />
                <span className="row">
                  <EventFeedback event={e} />
                  <FavoriteStar
                    kind="events"
                    id={e.id}
                    cameraId={e.camera_id}
                    favorite={e.favorite}
                  />
                </span>
              </CardFooter>
            </Card>
          </Fragment>
        ))}
      </div>

      <DetailPanel
        open={!!details}
        title={
          details && (
            <DrawerTitle
              title={details.label}
              sub={`${cameraName(details.camera_id)} – ${localTime(details.first_ts)}`}
            />
          )
        }
        onClose={() => setDetails(null)}
      >
        {details && <EventDetail event={details} />}
      </DetailPanel>

      <Dialog size="large" open={!!playing} onOpenChange={(_, d) => !d.open && setPlaying(null)}>
        <DialogSurface className="clip-dialog">
          {playing && (
            <DialogBody>
              <DialogTitle
                action={
                  <Button
                    appearance="subtle"
                    aria-label={t("common.close")}
                    icon={<Dismiss24Regular />}
                    onClick={() => setPlaying(null)}
                  />
                }
              >
                {playing.label} – {cameraName(playing.camera_id)} –{" "}
                <span className="nowrap">{localTime(playing.first_ts)}</span>
              </DialogTitle>
              <DialogContent>
                {playing.clip_url && (
                  <div className="detail-media">
                    <VideoPlayer
                      title={`${playing.label} – ${cameraName(playing.camera_id)}`}
                      src={playing.clip_url}
                      poster={playing.thumb_url}
                      thumbnails={`/api/events/${playing.id}/thumbnails.vtt`}
                      motion={playingTimeline.motion}
                      detections={playingTimeline.detections}
                      posterDetections={eventThumbBox(playing)}
                      gaps={playingTimeline.gaps}
                      autoPlay
                    />
                  </div>
                )}
              </DialogContent>
              <DialogActions>
                <DownloadButton href={`${playing.clip_url}?download=true`}>
                  {t("common.download")}
                </DownloadButton>
                <Button appearance="primary" onClick={() => setPlaying(null)}>
                  {t("common.close")}
                </Button>
              </DialogActions>
            </DialogBody>
          )}
        </DialogSurface>
      </Dialog>
    </>
  );
}
