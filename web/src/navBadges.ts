// The number on the Events nav button (events since you last opened Events), and the
// backlog of each kind of job (admins: the top bar's counts and the Jobs tabs).
import { useQuery } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";

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

/** What a count is of (its words, see `CountBadge`). */
export type BadgeKind = "events";

export interface NavBadge {
  count: number;
  kind: BadgeKind;
}

export type JobCounts = Record<"preparation" | "archiving" | "detection", number>;

const NO_JOBS: JobCounts = { preparation: 0, archiving: 0, detection: 0 };

// One live connection for every component showing the counts, open while any is mounted.
// Server-sent events (/api/live/jobs): the server sends the counts whenever they change;
// the browser reconnects by itself when the connection drops.
let latest: JobCounts = NO_JOBS;
let source: EventSource | null = null;
const watchers = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  watchers.add(onChange);
  if (!source) {
    source = new EventSource("/api/live/jobs");
    source.addEventListener("message", (e: MessageEvent<string>) => {
      latest = JSON.parse(e.data) as JobCounts;
      watchers.forEach((w) => w());
    });
  }
  return () => {
    watchers.delete(onChange);
    if (!watchers.size) {
      source?.close();
      source = null;
    }
  };
}

/** Jobs' backlog (admins), live: recordings waiting for the server to prepare them,
 *  periods waiting to be archived, and detection jobs waiting or running. */
export function useJobCounts(): JobCounts {
  return useSyncExternalStore(subscribe, () => latest);
}

export function useNavBadges(onEvents: boolean): Record<string, NavBadge[]> {
  const events = useQuery({
    queryKey: ["events", "nav"],
    queryFn: () => api.events({ limit: 50, hide_wrong: true }),
    refetchInterval: 30_000,
  });
  // Looking at Events marks everything up to now as seen (also what arrives meanwhile).
  const updated = events.dataUpdatedAt;
  useEffect(() => {
    if (!onEvents) return;
    const mark = () => localStorage.setItem(SEEN_KEY, String(Date.now()));
    mark();
    return mark; // and when leaving
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- again on each refetch
  }, [onEvents, updated]);
  const seen = seenAt();
  const newEvents = onEvents
    ? 0
    : (events.data ?? []).filter((e) => Date.parse(e.first_ts) > seen).length;
  return { "/events": [{ count: newEvents, kind: "events" }] };
}
