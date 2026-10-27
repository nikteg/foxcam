// Motion detection's results, the same way everywhere (pipeline/motion.py, still.py).
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type Detection, type MotionFrame } from "../api";
import { percent } from "../format";
import { useI18n } from "../i18n";
import { NoValue, Tag } from "./ui";

/** Where things moved in an event's clip or an archive (that video's own time); none
 *  while another video's is still on screen. */
export function useVideoMotion(kind: "events" | "archives", id: number | null): MotionFrame[] {
  const q = useQuery({
    queryKey: ["video-motion", kind, id],
    queryFn: () => api.videoMotion(kind, id!),
    enabled: id !== null,
    placeholderData: keepPreviousData,
  });
  return (!q.isPlaceholderData && q.data) || [];
}

/** How much of a recording's picture moved: "12% of the picture", "none" or "–". */
export function MotionShare({ score, short = false }: { score: number | null; short?: boolean }) {
  const { t } = useI18n();
  if (score === null) return <NoValue />;
  if (score === 0) return <span className="muted">{t("recordings.noMotion")}</span>;
  return short ? percent(score) : t("recordings.motionShare", { value: percent(score, 1) });
}

/** Whether a detection's box had motion under it, and whether it counts as standing still. */
export function DetectionMotion({ detection: d }: { detection: Detection }) {
  const { t } = useI18n();
  if (d.still)
    return (
      <Tag kind="warn" title={t("detections.stillHint")}>
        {t("detections.still")}
      </Tag>
    );
  if (d.moving === null) return <span className="muted">{t("detections.motionUnknown")}</span>;
  return d.moving ? (
    <Tag kind="ok">{t("detections.moving")}</Tag>
  ) : (
    <Tag>{t("detections.notMoving")}</Tag>
  );
}
