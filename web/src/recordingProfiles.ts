// Recording profiles (quality of the stored footage): the list, and a one-line summary.
import { useQuery } from "@tanstack/react-query";

import { api, type RecordingProfile } from "./api";
import { useI18n } from "./i18n";

export function useRecordingProfiles() {
  return useQuery({ queryKey: ["recording-profiles"], queryFn: api.recordingProfiles });
}

/** "≤ 720p, 1200 kbit/s, 15 fps, H.264, kept 30 days" */
export function useRecordingSummary(): (p: RecordingProfile) => string {
  const { t } = useI18n();
  return (p) => {
    const parts = p.transcode
      ? [
          p.max_height ? `≤ ${p.max_height}p` : t("recording.sameResolution"),
          p.bitrate_kbit ? `${p.bitrate_kbit} kbit/s` : t("recording.constantQuality"),
          ...(p.fps ? [`${p.fps} fps`] : []),
          p.codec === "h265" ? "H.265" : "H.264",
        ]
      : [t("recording.asSent")];
    if (p.keep_days !== null) parts.push(t("recording.keptDays", { n: p.keep_days }));
    return parts.join(", ");
  };
}
