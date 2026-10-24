// Soft warnings for a detection profile's targets (never block saving).
import type { LabelSuggestions } from "./api";

export type ProfileWarning =
  /** Targets no worker that may run the profile can report: they never create events. */
  | { kind: "unreportable"; labels: string[]; detectors: string[] }
  /** Targets that look like synonyms, on a profile open-vocabulary workers can run. */
  | { kind: "synonyms"; labels: string[] };

const lower = (s: string) => s.trim().toLowerCase();

export function profileWarnings(
  targets: string[],
  aliases: Record<string, string>,
  requires: string[],
  data: LabelSuggestions | undefined,
): ProfileWarning[] {
  if (!data) return [];
  const need = requires.map(lower);
  const eligible = data.workers.filter((w) => {
    const tags = new Set(w.tags.map(lower));
    return need.every((t) => tags.has(t));
  });
  const open = eligible.filter((w) => w.open_vocabulary);
  const closed = eligible.filter((w) => !w.open_vocabulary);
  const out: ProfileWarning[] = [];

  // Only when every worker that may run it has a known, closed vocabulary: an
  // open-vocabulary worker can report anything, an unknown one might.
  if (eligible.length > 0 && open.length === 0 && closed.every((w) => w.vocabulary)) {
    const vocab = new Set(closed.flatMap((w) => (w.vocabulary ?? []).map(lower)));
    const reportable = (target: string) =>
      vocab.has(lower(target)) ||
      Object.entries(aliases).some(
        ([from, to]) => lower(to) === lower(target) && vocab.has(lower(from)),
      );
    const missing = targets.filter((t) => !reportable(t));
    if (missing.length > 0) {
      const detectors = [...new Set(closed.map((w) => w.detector ?? w.id))];
      out.push({ kind: "unreportable", labels: missing, detectors });
    }
  }

  // Synonyms compete when prompted together (docs/MODELS.md), so only where an
  // open-vocabulary worker may run the profile.
  if (open.length > 0) {
    for (const group of data.synonym_groups) {
      const words = new Set(group);
      const hits = targets.filter((t) => words.has(lower(t)));
      if (hits.length >= 2) out.push({ kind: "synonyms", labels: hits });
    }
  }
  return out;
}
