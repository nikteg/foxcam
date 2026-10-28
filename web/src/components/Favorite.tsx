import { ToggleButton } from "@fluentui/react-components";
import { Star20Filled, Star20Regular } from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { useCanLabel } from "../auth";
import { useI18n } from "../i18n";
import { refreshLabels } from "./Feedback";
import { useToast } from "./Toasts";

/** The star of a detection or an event (an event's goes on its best detection, and off
 * all of them). Editors of the camera change it; others see it. */
export function FavoriteStar({
  kind,
  id,
  cameraId,
  favorite,
  withLabel = false,
}: {
  kind: "detections" | "events";
  id: number;
  cameraId: string;
  favorite: boolean;
  /** In a drawer: a button with its name; in rows and cards: the star alone. */
  withLabel?: boolean;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const toast = useToast();
  const canLabel = useCanLabel();
  const mutation = useMutation({
    mutationFn: (on: boolean) => api.favorite(kind, id, on),
    onSuccess: () => refreshLabels(client),
    onError: (e) => toast.error(e),
  });
  // The pending choice at once.
  const on = mutation.isPending ? mutation.variables : favorite;
  const label = on ? t("favorite.remove") : t("favorite.add");
  return (
    <ToggleButton
      size={withLabel ? "medium" : "small"}
      appearance={withLabel ? "secondary" : "subtle"}
      className="favorite-star"
      checked={on}
      icon={on ? <Star20Filled className="star-icon" /> : <Star20Regular />}
      disabled={!canLabel(cameraId)}
      onClick={() => mutation.mutate(!on)}
      aria-label={withLabel ? undefined : label}
      // A native tooltip: hundreds of Fluent Tooltips make lists slow.
      title={label}
    >
      {withLabel && t("favorite.label")}
    </ToggleButton>
  );
}
