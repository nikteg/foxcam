// Picking several rows (Detections) or cards (Events) and changing them at once: feedback
// and stars. The selection is the page's own state (not the URL: it can be hundreds of
// ids); rows that leave the list (a filter) leave the selection too.
import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Text,
} from "@fluentui/react-components";
import {
  CheckmarkCircle20Regular,
  Dismiss20Regular,
  DismissCircle20Regular,
  Eraser20Regular,
  ArrowSync20Regular,
  MoreHorizontal20Regular,
  SelectAllOn20Regular,
  Star20Regular,
  StarOff20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";

import { ConfirmPopover } from "./ConfirmPopover";

import { api, type BulkChanges } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";
import { refreshLabels } from "./Feedback";
import { useToast } from "./Toasts";

export interface Selection<T> {
  isSelected: (item: T) => boolean;
  canSelect: (item: T) => boolean;
  /** With Shift: everything between the last one picked and this one (in `ordered`, the
   * order on screen) gets this one's new state. */
  toggle: (item: T, shift: boolean, ordered: T[]) => void;
  setAll: (items: T[], on: boolean) => void;
  clear: () => void;
  /** The selected items still in the list. */
  chosen: T[];
}

/** Shift held on a checkbox's change (picks a range). */
export const withShift = (e: { nativeEvent: Event }) =>
  (e.nativeEvent as globalThis.MouseEvent).shiftKey === true;

/** Which of `items` are picked; only those `canSelect` allows (cameras the user labels). */
export function useSelection<T>(
  items: T[] | undefined,
  getId: (item: T) => number,
  canSelect: (item: T) => boolean,
): Selection<T> {
  const [ids, setIds] = useState<ReadonlySet<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const isSelected = (item: T) => ids.has(getId(item));
  const setMany = (list: T[], on: boolean) =>
    setIds((prev) => {
      const next = new Set(prev);
      for (const item of list) {
        if (!canSelect(item)) continue;
        if (on) next.add(getId(item));
        else next.delete(getId(item));
      }
      return next;
    });
  return {
    isSelected,
    canSelect,
    toggle: (item, shift, ordered) => {
      const on = !isSelected(item);
      const from = ordered.findIndex((i) => getId(i) === anchor);
      const to = ordered.indexOf(item);
      if (shift && from >= 0 && to >= 0) {
        setMany(ordered.slice(Math.min(from, to), Math.max(from, to) + 1), on);
      } else {
        setMany([item], on);
      }
      setAnchor(getId(item));
    },
    setAll: setMany,
    clear: () => setIds(new Set()),
    chosen: (items ?? []).filter(isSelected),
  };
}

/** Under the list while something is picked, kept in sight at the bottom of the screen:
 * how many, and what to do with them. */
export function BulkBar<T>({
  kind,
  selection,
  items,
  getId,
}: {
  kind: "detections" | "events";
  selection: Selection<T>;
  /** The whole list, in screen order ("Select all"). */
  items: T[];
  getId: (item: T) => number;
}) {
  const { t } = useI18n();
  const phone = useMediaQuery(PHONE);
  const client = useQueryClient();
  const toast = useToast();
  const { chosen } = selection;
  // "Run detection again" asks first, at the More button it was picked from.
  const [more, setMore] = useState<HTMLElement | null>(null);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const rerun = async () => {
    const r = await api.bulkReanalyse(kind, chosen.map(getId));
    await refreshLabels(client);
    toast.success(
      r.skipped
        ? t("bulk.rerunSkipped", { count: r.queued, skipped: r.skipped })
        : t("bulk.rerun", { count: r.queued }),
    );
  };
  const selectable = items.filter(selection.canSelect);
  const mutation = useMutation({
    mutationFn: ({ changes }: { changes: BulkChanges; done: MessageKey }) =>
      api.bulk(kind, chosen.map(getId), changes),
    onSuccess: async (r, { done }) => {
      await refreshLabels(client);
      toast.success(t(done, { count: r.changed }));
    },
    onError: (e) => toast.error(e),
  });
  if (chosen.length === 0) return null;
  const apply = (changes: BulkChanges, done: MessageKey) => mutation.mutate({ changes, done });
  // Phones: icons only (the bar fits one row at 390 px).
  const action = (label: string, icon: ReactElement, onClick: () => void) => (
    <Button
      appearance="subtle"
      icon={icon}
      disabled={mutation.isPending}
      onClick={onClick}
      aria-label={phone ? label : undefined}
      title={phone ? label : undefined}
    >
      {!phone && label}
    </Button>
  );
  return (
    <div className="bulk-bar" role="toolbar" aria-label={t("bulk.toolbar")}>
      <Text weight="semibold" className="bulk-count">
        {t("bulk.selected", { count: chosen.length })}
      </Text>
      {action(t("feedback.correct"), <CheckmarkCircle20Regular />, () =>
        apply({ feedback: true }, "bulk.markedCorrect"),
      )}
      {action(t("feedback.wrong"), <DismissCircle20Regular />, () =>
        apply({ feedback: false }, "bulk.markedWrong"),
      )}
      {action(t("favorite.label"), <Star20Regular />, () =>
        apply({ favorite: true }, "bulk.starred"),
      )}
      <Menu positioning="above-end">
        <MenuTrigger disableButtonEnhancement>
          <Button
            ref={setMore}
            appearance="subtle"
            icon={<MoreHorizontal20Regular />}
            aria-label={t("bulk.more")}
            title={t("bulk.more")}
            disabled={mutation.isPending}
          />
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            {chosen.length < selectable.length && (
              <MenuItem
                icon={<SelectAllOn20Regular />}
                onClick={() => selection.setAll(selectable, true)}
              >
                {t("bulk.selectAll", { count: selectable.length })}
              </MenuItem>
            )}
            <MenuItem
              icon={<StarOff20Regular />}
              onClick={() => apply({ favorite: false }, "bulk.unstarred")}
            >
              {t("favorite.remove")}
            </MenuItem>
            <MenuItem
              icon={<Eraser20Regular />}
              onClick={() => apply({ feedback: null }, "bulk.feedbackCleared")}
            >
              {t("bulk.clearFeedback")}
            </MenuItem>
            <MenuItem icon={<ArrowSync20Regular />} onClick={() => setConfirmRerun(true)}>
              {t("bulk.rerunAction")}
            </MenuItem>
          </MenuList>
        </MenuPopover>
      </Menu>
      <ConfirmPopover
        target={more}
        open={confirmRerun}
        onOpenChange={setConfirmRerun}
        title={t("bulk.rerunTitle", { count: chosen.length })}
        body={t(kind === "events" ? "bulk.rerunBodyEvents" : "bulk.rerunBodyDetections")}
        confirmLabel={t("bulk.rerunConfirm")}
        icon={<ArrowSync20Regular />}
        onConfirm={rerun}
      />
      <Button
        appearance="subtle"
        icon={<Dismiss20Regular />}
        aria-label={t("bulk.clear")}
        title={t("bulk.clear")}
        onClick={selection.clear}
      />
    </div>
  );
}
