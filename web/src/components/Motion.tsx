// Motion detection's results, the same way everywhere (pipeline/motion.py, still.py).
import type { Detection } from "../api";
import { percent } from "../format";
import { useI18n } from "../i18n";
import { NoValue, Tag } from "./ui";

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
