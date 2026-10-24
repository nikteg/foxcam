import { useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
  );
}

/** Phone layout: bottom tab bar, tables rendered as cards. Keep in sync with styles.css. */
export const PHONE = "(max-width: 720px)";
/** Wide enough to show a detail panel next to the list instead of over it. */
export const WIDE = "(min-width: 1100px)";
