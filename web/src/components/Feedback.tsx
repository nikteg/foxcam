import { ToggleButton, Tooltip } from "@fluentui/react-components";
import {
  CheckmarkCircle20Filled,
  CheckmarkCircle20Regular,
  DismissCircle20Filled,
  DismissCircle20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type Detection } from "../api";
import { useI18n } from "../i18n";

/** ✓ / ✗ training feedback for a detection; clicking the active choice clears it. */
export function Feedback({
  detection,
  withLabels = false,
}: {
  detection: Detection;
  withLabels?: boolean;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (correct: boolean | null) => api.feedback(detection.id, correct),
    onSuccess: () => client.invalidateQueries({ queryKey: ["detections"] }),
  });
  // Show the pending choice immediately.
  const value = mutation.isPending ? mutation.variables : detection.feedback;
  const toggle = (want: boolean) => mutation.mutate(value === want ? null : want);
  const size = withLabels ? "medium" : "small";
  return (
    <span className="row">
      <Tooltip content={t("feedback.correct")} relationship="label">
        <ToggleButton
          size={size}
          appearance={withLabels ? "outline" : "subtle"}
          checked={value === true}
          icon={
            value === true ? (
              <CheckmarkCircle20Filled className="ok-icon" />
            ) : (
              <CheckmarkCircle20Regular />
            )
          }
          onClick={() => toggle(true)}
        >
          {withLabels && t("feedback.correct")}
        </ToggleButton>
      </Tooltip>
      <Tooltip content={t("feedback.wrong")} relationship="label">
        <ToggleButton
          size={size}
          appearance={withLabels ? "outline" : "subtle"}
          checked={value === false}
          icon={
            value === false ? (
              <DismissCircle20Filled className="bad-icon" />
            ) : (
              <DismissCircle20Regular />
            )
          }
          onClick={() => toggle(false)}
        >
          {withLabels && t("feedback.wrong")}
        </ToggleButton>
      </Tooltip>
    </span>
  );
}
