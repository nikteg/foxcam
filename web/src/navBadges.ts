// Numbers on the Events and Jobs nav buttons: events since you last opened Events; on Jobs
// (admins) the backlog of each kind of job, the same numbers as on the Jobs tabs.
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "./api";

const SEEN_KEY = "foxcam.eventsSeen";

/** When Events was last open (first visit: now, so old events are not "new"). */
function seenAt(): number {
  const stored = Number(localStorage.getItem(SEEN_KEY));
  if (stored) return stored;
  const now = Date.now();
  localStorage.setItem(SEEN_KEY, String(now));
  return now;
}

/** What a count is of (its colour and words, see `CountBadge`). */
export type BadgeKind = "events" | "detection" | "preparation" | "archiving";

export interface NavBadge {
  count: number;
  kind: BadgeKind;
}

/** Jobs' backlog (admins): detection jobs waiting for or running on a worker, recordings
 *  waiting for the server to prepare them, and periods waiting to be archived. */
export function useJobCounts(
  enabled: boolean,
): Record<"detection" | "preparation" | "archiving", number> {
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 30_000,
    enabled,
  });
  const d = status.data;
  return {
    detection: (d?.queue.counts.pending ?? 0) + (d?.queue.counts.leased ?? 0),
    preparation: (d?.preprocessing?.waiting ?? 0) + (d?.preprocessing?.preparing ?? 0),
    archiving: d?.compaction?.waiting_periods ?? 0,
  };
}

export function useNavBadges(isAdmin: boolean, onEvents: boolean): Record<string, NavBadge[]> {
  const events = useQuery({
    queryKey: ["events", "nav"],
    queryFn: () => api.events({ limit: 50, hide_wrong: true }),
    refetchInterval: 30_000,
  });
  const jobs = useJobCounts(isAdmin);
  // Looking at Events marks everything up to now as seen (also what arrives meanwhile).
  const updated = events.dataUpdatedAt;
  useEffect(() => {
    if (!onEvents) return;
    const mark = () => localStorage.setItem(SEEN_KEY, String(Date.now()));
    mark();
    return mark; // and when leaving
  }, [onEvents, updated]);
  const seen = seenAt();
  const newEvents = onEvents
    ? 0
    : (events.data ?? []).filter((e) => Date.parse(e.first_ts) > seen).length;
  return {
    "/events": [{ count: newEvents, kind: "events" }],
    "/jobs": isAdmin
      ? [
          // In the order of the Jobs tabs.
          { count: jobs.preparation, kind: "preparation" },
          { count: jobs.archiving, kind: "archiving" },
          { count: jobs.detection, kind: "detection" },
        ]
      : [],
  };
}
