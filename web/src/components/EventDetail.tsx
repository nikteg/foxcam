// One event in the details drawer (components/DetailPanel.tsx, opened from an event card's
// picture): its best detection's frame with the box, the feedback for it, and the event's
// facts. "All detections" opens the Detections tab filtered to the event.
import { Button } from "@fluentui/react-components";
import { ScanObject20Regular } from "@fluentui/react-icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, detectionFrameUrl, type DetectionEvent, type MotionFrame } from "../api";
import { duration } from "../format";
import { useI18n } from "../i18n";
import { CameraTag } from "./CameraSelect";
import { Feedback } from "./Feedback";
import { ProfileTag, useDetectorNames, useProfiles, useSeveralDetectors } from "./ProfileSelect";
import { ConfidenceBar, Fact, Facts, Light, QueryState, Tag, Time, useRouterHref } from "./ui";
import { VideoPlayer } from "./VideoPlayer";
import { DetectionMotion } from "./Motion";

/** The event's detections, with its best one open. */
const detectionsHref = (e: DetectionEvent) =>
  `/events/detections?event=${e.id}&only_targets=true` +
  (e.best_detection_id ? `&selected=${e.best_detection_id}` : "");

/** Where things moved in an event's clip (clip time); none while another event's is still
 * on screen. */
export function useClipMotion(eventId: number | null): MotionFrame[] {
  const q = useQuery({
    queryKey: ["event-motion", eventId],
    queryFn: () => api.eventMotion(eventId!),
    enabled: eventId !== null,
    placeholderData: keepPreviousData,
  });
  return (!q.isPlaceholderData && q.data) || [];
}

export function EventDetail({ event: e }: { event: DetectionEvent }) {
  const { t } = useI18n();
  const routerHref = useRouterHref();
  const manyProfiles = (useProfiles().data?.length ?? 0) > 1;
  const severalDetectors = useSeveralDetectors();
  const detectorNames = useDetectorNames();
  const detections = useQuery({
    queryKey: ["detections", "event", e.id],
    queryFn: () => api.detections({ event: e.id, only_targets: true, limit: 500 }),
    // Another event's list stays until the new one arrives: the drawer keeps its height
    // and scroll position (comparing events).
    placeholderData: keepPreviousData,
  });
  const best = detections.data?.find((d) => d.id === e.best_detection_id) ?? detections.data?.[0];
  // Where things moved: in the clip (its own clock), or in the recording shown until the
  // clip is cut. The previous event's stays until this one's arrives, but is not drawn.
  const clipMotion = useClipMotion(e.clip_url ? e.id : null);
  const recording = useQuery({
    queryKey: ["segment", best?.segment_id],
    queryFn: () => api.segment(best!.segment_id),
    enabled: !e.clip_url && !!best,
    placeholderData: keepPreviousData,
  });
  const motion = e.clip_url
    ? clipMotion
    : (!recording.isPlaceholderData && recording.data?.motion_boxes) || [];

  return (
    <div className="detail-stack">
      <QueryState error={detections.error} loading={detections.isPending} />
      {/* The event's clip (with pre- and post-roll; its poster is the best frame with the
          box); until it is cut, the recording from just before the best detection. */}
      {/* One player in both cases: switching events keeps it (and the drawer's scroll). */}
      {(e.clip_url || best) && (
        <VideoPlayer
          title={e.label}
          motion={motion}
          {...(e.clip_url || !best
            ? {
                src: e.clip_url!,
                poster: e.thumb_url,
                thumbnails: `/api/events/${e.id}/thumbnails.vtt`,
              }
            : {
                src: `/api/segments/${best.segment_id}/play`,
                poster: detectionFrameUrl(best),
                startAt: Math.max(best.offset_ms / 1000 - 1, 0),
                thumbnails: `/api/segments/${best.segment_id}/thumbnails.vtt`,
              })}
        />
      )}
      {best && (
        <span className="row wrap">
          <Feedback detection={best} withLabels />
        </span>
      )}
      <Facts>
        <Fact label={t("common.camera")}>
          <CameraTag id={e.camera_id} />
        </Fact>
        <Fact label={t("col.time")}>
          <Time iso={e.first_ts} />
        </Fact>
        {manyProfiles && (
          <Fact label={t("profiles.profile")}>
            <ProfileTag id={e.profile_id} />
          </Fact>
        )}
        <Fact label={t("col.confidence")}>
          <ConfidenceBar value={e.max_confidence} />
        </Fact>
        <Fact label={t("col.light")}>
          <Light night={e.is_night} long />
        </Fact>
        {best && (
          <Fact label={t("detections.motion")}>
            <DetectionMotion detection={best} />
          </Fact>
        )}
        <Fact label={t("nav.detections")}>{e.detection_count}</Fact>
        {/* Until the clip exists, the player above shows the whole recording. */}
        <Fact label={t("col.length")}>
          {e.clip_url ? (
            duration(e.clip_duration_s)
          ) : e.clip_error ? (
            <Tag kind="bad" title={e.clip_error}>
              {t("events.clipFailed")}
            </Tag>
          ) : (
            <Tag kind="pending">{t("events.buildingClip")}</Tag>
          )}
        </Fact>
        {severalDetectors && e.detectors.length > 0 && (
          <Fact label={t("events.seenByModels")}>
            <span className="row wrap tags">
              {e.detectors.map((d) => (
                <Tag key={d}>{detectorNames.get(d) ?? d}</Tag>
              ))}
            </span>
          </Fact>
        )}
      </Facts>
      {(e.feedback === "wrong" || !e.confirmed) && (
        <span className="row wrap tags">
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
        </span>
      )}
      <Button as="a" icon={<ScanObject20Regular />} {...routerHref(detectionsHref(e))}>
        {t("events.allDetections")}
      </Button>
    </div>
  );
}
