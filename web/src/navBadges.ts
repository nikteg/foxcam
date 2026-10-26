// Numbers on the Events and Jobs nav buttons: events since you last opened Events; on Jobs
// (admins) two at once: detection jobs waiting for or running on a worker, and recordings
// waiting for the server to prepare them (they have no jobs yet).
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

/** One number on a nav button; ``server``: the server's own backlog (a second, neutral badge). */
export interface NavBadge {
  count: number;
  kind: "main" | "server";
}

export function useNavBadges(isAdmin: boolean, onEvents: boolean): Record<string, NavBadge[]> {
  const events = useQuery({
    queryKey: ["events", "nav"],
    queryFn: () => api.events({ limit: 50, hide_wrong: true }),
    refetchInterval: 30_000,
  });
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 30_000,
    enabled: isAdmin,
  });
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
  const counts = status.data?.queue.counts;
  const preparing = status.data?.segments.new ?? 0;
  return {
    "/events": [{ count: newEvents, kind: "main" }],
    "/jobs":
      isAdmin && counts
        ? [
            { count: (counts.pending ?? 0) + (counts.leased ?? 0), kind: "main" },
            { count: preparing, kind: "server" },
          ]
        : [],
  };
}
