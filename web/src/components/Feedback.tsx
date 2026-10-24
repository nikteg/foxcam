import { ToggleButton, Tooltip } from "@fluentui/react-components";
import {
  CheckmarkCircle20Filled,
  CheckmarkCircle20Regular,
  DismissCircle20Filled,
  DismissCircle20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactElement } from "react";

import { api, type Detection } from "../api";
import { hasRole, useCameraRole } from "../auth";
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
  // Labelling is for editors of the camera; viewers see the choice but cannot change it.
  const role = useCameraRole(detection.camera_id);
  const readOnly = !hasRole(role ? { role } : undefined, "editor");
  const button = (want: boolean, label: string, on: ReactElement, off: ReactElement) => {
    const toggleButton = (
      <ToggleButton
        size={size}
        appearance={withLabels ? "outline" : "subtle"}
        checked={value === want}
        icon={value === want ? on : off}
        disabled={readOnly}
        onClick={() => toggle(want)}
        // In table rows a native tooltip: hundreds of Fluent Tooltips make lists slow.
        aria-label={withLabels ? undefined : label}
        title={withLabels ? undefined : label}
      >
        {withLabels && label}
      </ToggleButton>
    );
    return withLabels ? (
      <Tooltip content={label} relationship="description">
        {toggleButton}
      </Tooltip>
    ) : (
      toggleButton
    );
  };
  return (
    <span className="row">
      {button(
        true,
        t("feedback.correct"),
        <CheckmarkCircle20Filled className="ok-icon" />,
        <CheckmarkCircle20Regular />,
      )}
      {button(
        false,
        t("feedback.wrong"),
        <DismissCircle20Filled className="bad-icon" />,
        <DismissCircle20Regular />,
      )}
    </span>
  );
}
