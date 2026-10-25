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
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["events"] }); // shown or hidden as wrong
      // A wrong detection is no match: the recording's and archive's match counts follow.
      for (const key of ["segments", "segment", "segment-jobs", "archives", "status"]) {
        void client.invalidateQueries({ queryKey: [key] });
      }
      // Pending until the detection comes back with its new feedback, so the button
      // doesn't flash back to the old choice in between.
      return client.invalidateQueries({ queryKey: ["detections"] });
    },
  });
  // Show the pending choice immediately.
  const value = mutation.isPending ? mutation.variables : detection.feedback;
  const toggle = (want: boolean) => mutation.mutate(value === want ? null : want);
  const size = withLabels ? "medium" : "small";
  // Labelling is for editors of the camera; viewers see the choice but cannot change it.
  const role = useCameraRole(detection.camera_id);
  const readOnly = !hasRole(role ? { role } : undefined, "editor");
  const button = (
    want: boolean,
    label: string,
    hint: string,
    on: ReactElement,
    off: ReactElement,
  ) => {
    const checked = value === want;
    const toggleButton = (
      <ToggleButton
        size={size}
        appearance={withLabels ? "outline" : "subtle"}
        checked={checked}
        // With labels the chosen button is filled in its colour (styles.css).
        className={withLabels && checked ? `feedback-on ${want ? "ok" : "bad"}` : undefined}
        icon={checked ? on : off}
        disabled={readOnly}
        onClick={() => toggle(want)}
        // In table rows a native tooltip: hundreds of Fluent Tooltips make lists slow.
        aria-label={withLabels ? undefined : label}
        title={withLabels ? undefined : hint}
      >
        {withLabels && label}
      </ToggleButton>
    );
    return withLabels ? (
      <Tooltip content={hint} relationship="description">
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
        t("feedback.correctHint"),
        <CheckmarkCircle20Filled className="ok-icon" />,
        <CheckmarkCircle20Regular />,
      )}
      {button(
        false,
        t("feedback.wrong"),
        t("feedback.wrongHint"),
        <DismissCircle20Filled className="bad-icon" />,
        <DismissCircle20Regular />,
      )}
    </span>
  );
}
