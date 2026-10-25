// Numbers on the Events and Jobs nav buttons: events since you last opened Events, and
// jobs waiting for or running on a worker (admins).
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

export function useNavBadges(isAdmin: boolean, onEvents: boolean): Record<string, number> {
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
  return {
    "/events": newEvents,
    "/jobs": isAdmin && counts ? (counts.pending ?? 0) + (counts.leased ?? 0) : 0,
  };
}
