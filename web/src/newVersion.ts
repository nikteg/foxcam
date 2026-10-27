// A page left open across a deploy runs the old app against the new server, whose API may
// have changed. Every API response says which build the server serves (X-Foxcam-Build,
// from index.html, see vite.config.ts); when it is not this page's, offer to reload.
import { useSyncExternalStore } from "react";

declare const __BUILD_ID__: string;

let newer = false;
const listeners = new Set<() => void>();

/** Look at an API response's build (api.ts calls this for each one). */
export function noteBuild(res: Response) {
  const served = res.headers.get("X-Foxcam-Build");
  if (newer || !served || !__BUILD_ID__ || served === __BUILD_ID__) return;
  newer = true;
  for (const listener of listeners) listener();
}

/** Whether the server serves a newer build than this page runs. */
export function useNewerBuild(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => newer,
  );
}
