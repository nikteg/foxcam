import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  CardPreview,
  Text,
} from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  Dismiss24Regular,
  Play20Filled,
  Search20Regular,
} from "@fluentui/react-icons";
import { Fragment, useState } from "react";

import type { DetectionEvent } from "../api";
import { duration, localDay, localTime, percent } from "../format";
import { useI18n } from "../i18n";
import { useCameraName } from "./CameraSelect";
import {
  useDetectorNames,
  useProfileName,
  useProfiles,
  useSeveralDetectors,
} from "./ProfileSelect";
import { Empty, Light, Tag, Time, useRouterHref } from "./ui";
import { VideoPlayer } from "./VideoPlayer";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";

export function EventsGrid({
  events,
  byDay = false,
}: {
  events: DetectionEvent[];
  /** A heading per day (the Events page; the dashboard shows only the latest few). */
  byDay?: boolean;
}) {
  const { t } = useI18n();
  const routerHref = useRouterHref();
  const [playing, setPlaying] = useState<DetectionEvent | null>(null);
  const profileName = useProfileName();
  const cameraName = useCameraName();
  const manyProfiles = (useProfiles().data?.length ?? 0) > 1;
  const severalDetectors = useSeveralDetectors();
  const detectorNames = useDetectorNames();
  if (events.length === 0) return <Empty>{t("events.none")}</Empty>;

  return (
    <>
      <div className="cards">
        {events.map((e, i) => (
          <Fragment key={e.id}>
            {byDay && (i === 0 || localDay(events[i - 1]!.first_ts) !== localDay(e.first_ts)) && (
              <h2 className="cards-day">{localDay(e.first_ts)}</h2>
            )}
            <Card className="event-card">
              <CardPreview>
                {e.thumb_url ? (
                  <button
                    type="button"
                    className="thumb-button"
                    disabled={!e.clip_url}
                    onClick={() => setPlaying(e)}
                    aria-label={t("events.playClip", { label: e.label })}
                  >
                    <img
                      className="thumb"
                      loading="lazy"
                      src={e.thumb_url}
                      alt={t("events.thumbAlt", {
                        label: e.label,
                        camera: cameraName(e.camera_id),
                      })}
                    />
                    {e.clip_url && (
                      <span className="play-overlay" aria-hidden="true">
                        <Play20Filled />
                      </span>
                    )}
                  </button>
                ) : (
                  <div className="thumb placeholder">
                    {e.building ? t("events.buildingClip") : t("events.noThumbnail")}
                  </div>
                )}
              </CardPreview>
              <CardHeader
                header={
                  <span className="row">
                    <Text weight="semibold">{e.label}</Text>
                    <Text weight="bold" className="brand-text">
                      {percent(e.max_confidence)}
                    </Text>
                    <Light night={e.is_night} />
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
              <CardFooter>
                {e.clip_url ? (
                  <>
                    <Button
                      appearance="primary"
                      icon={<Play20Filled />}
                      onClick={() => setPlaying(e)}
                    >
                      {t("common.play")}
                    </Button>
                    <Button
                      as="a"
                      href={`${e.clip_url}?download=true`}
                      icon={<ArrowDownload20Regular />}
                    >
                      {t("common.download")}
                    </Button>
                  </>
                ) : e.clip_error ? (
                  <Tag kind="bad" title={e.clip_error}>
                    {t("events.clipFailed")}
                  </Tag>
                ) : (
                  <Tag kind="pending">{t("events.buildingClip")}</Tag>
                )}
                <Button
                  as="a"
                  appearance="subtle"
                  className="push-end"
                  icon={<Search20Regular />}
                  title={t("events.showDetectionsHint")}
                  {...routerHref(`/events/detections?event=${e.id}&only_targets=true`)}
                >
                  {t("events.showDetections")}
                </Button>
              </CardFooter>
            </Card>
          </Fragment>
        ))}
      </div>

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
                      autoPlay
                    />
                  </div>
                )}
              </DialogContent>
              <DialogActions>
                <Button
                  as="a"
                  href={`${playing.clip_url}?download=true`}
                  icon={<ArrowDownload20Regular />}
                >
                  {t("common.download")}
                </Button>
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
