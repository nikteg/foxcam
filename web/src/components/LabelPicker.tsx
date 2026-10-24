// Pickers for detection labels (profile targets and aliases, notification filters), with
// suggestions from what the workers' models can report, the open-vocabulary lists and the
// labels seen so far. Free text is always allowed: any noun works for YOLO-World.
import {
  Combobox,
  Option,
  Tag as FluentTag,
  TagPicker,
  TagPickerControl,
  TagPickerGroup,
  TagPickerInput,
  TagPickerList,
  TagPickerOption,
} from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, type LabelSuggestions } from "../api";
import { percent } from "../format";
import { useI18n } from "../i18n";

const MAX_OPTIONS = 40;

export interface Suggestion {
  label: string;
  /** Where it comes from: detector names, "open vocabulary", "seen 12x, up to 91%"... */
  sources: string[];
  /** Times seen in detections (sorts first). */
  seen: number;
}

export function useLabelSuggestions() {
  return useQuery({ queryKey: ["labels"], queryFn: api.labels, staleTime: 60_000 });
}

/** One entry per label (case-insensitive), with all its sources. ``extra``: more labels to
 * suggest, e.g. the profiles' targets for notification filters. */
export function useSuggestionList(
  data: LabelSuggestions | undefined,
  extra: { labels: string[]; source: string } | null = null,
): Suggestion[] {
  const { t } = useI18n();
  return useMemo(() => {
    const byKey = new Map<string, Suggestion>();
    const add = (label: string, source: string, seen = 0) => {
      const key = label.toLowerCase();
      const s = byKey.get(key) ?? { label, sources: [], seen: 0 };
      if (!s.sources.includes(source)) s.sources.push(source);
      s.seen = Math.max(s.seen, seen);
      byKey.set(key, s);
    };
    for (const r of data?.seen ?? []) {
      add(r.label, t("labels.seen", { n: r.count, conf: percent(r.max_confidence) }), r.count);
    }
    for (const label of extra?.labels ?? []) add(label, extra!.source);
    for (const w of data?.workers ?? []) {
      for (const label of w.vocabulary ?? []) add(label, w.detector ?? w.id);
    }
    const ov = data?.open_vocabulary;
    for (const label of [...(ov?.wildlife ?? []), ...(ov?.objects365 ?? []), ...(ov?.lvis ?? [])])
      add(label, t("labels.openVocabulary"));
    return [...byKey.values()];
  }, [data, extra, t]);
}

/** A suggestion: the label, and where it comes from on a second line. */
function OptionText({ label, detail }: { label: string; detail: string }) {
  return (
    <span className="label-option">
      {label}
      <span className="muted label-option-source">{detail}</span>
    </span>
  );
}

/** Best matches for what was typed: exact, then prefix, then seen often, then A-Z. */
function filterSuggestions(list: Suggestion[], query: string, exclude: string[]): Suggestion[] {
  const q = query.trim().toLowerCase();
  const skip = new Set(exclude.map((x) => x.toLowerCase()));
  const rank = (s: Suggestion) => {
    const l = s.label.toLowerCase();
    return l === q ? 0 : l.startsWith(q) ? 1 : 2;
  };
  return list
    .filter((s) => !skip.has(s.label.toLowerCase()) && (!q || s.label.toLowerCase().includes(q)))
    .toSorted((a, b) => rank(a) - rank(b) || b.seen - a.seen || a.label.localeCompare(b.label))
    .slice(0, MAX_OPTIONS);
}

/** Several labels, picked from suggestions or typed (Enter adds what was typed). */
export function LabelPicker({
  value,
  onChange,
  suggestions,
  placeholder,
  ariaLabel,
}: {
  value: string[];
  onChange: (labels: string[]) => void;
  suggestions: Suggestion[];
  placeholder?: string;
  ariaLabel: string;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const typed = query.trim();
  const options = filterSuggestions(suggestions, typed, value);
  const offerTyped =
    typed !== "" &&
    !value.some((v) => v.toLowerCase() === typed.toLowerCase()) &&
    !options.some((o) => o.label.toLowerCase() === typed.toLowerCase());

  return (
    <TagPicker
      selectedOptions={value}
      onOptionSelect={(_, d) => {
        // Selecting adds (the typed text or a suggestion); dismissing a tag removes it.
        const picked = d.value;
        onChange(value.includes(picked) ? value.filter((v) => v !== picked) : [...value, picked]);
        setQuery("");
      }}
    >
      <TagPickerControl>
        <TagPickerGroup aria-label={ariaLabel}>
          {value.map((label) => (
            <FluentTag key={label} shape="rounded" value={label}>
              {label}
            </FluentTag>
          ))}
        </TagPickerGroup>
        <TagPickerInput
          aria-label={ariaLabel}
          value={query}
          placeholder={value.length ? undefined : placeholder}
          onChange={(e) => setQuery(e.target.value)}
        />
      </TagPickerControl>
      <TagPickerList>
        {offerTyped && (
          <TagPickerOption value={typed} text={typed}>
            <OptionText label={typed} detail={t("labels.useTyped")} />
          </TagPickerOption>
        )}
        {options.map((s) => (
          <TagPickerOption key={s.label} value={s.label} text={s.label}>
            <OptionText label={s.label} detail={s.sources.join(", ")} />
          </TagPickerOption>
        ))}
      </TagPickerList>
    </TagPicker>
  );
}

/** One label, picked from suggestions or typed freely (alias rows). */
export function LabelCombobox({
  value,
  onChange,
  suggestions,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (label: string) => void;
  suggestions: Suggestion[];
  placeholder?: string;
  ariaLabel: string;
}) {
  const options = filterSuggestions(suggestions, value, []);
  return (
    <Combobox
      freeform
      aria-label={ariaLabel}
      placeholder={placeholder}
      // Only the text is controlled: a controlled selection would reset what is typed
      // whenever it matches no suggestion.
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onOptionSelect={(_, d) => onChange(d.optionValue ?? "")}
    >
      {options.map((s) => (
        <Option key={s.label} value={s.label} text={s.label}>
          <OptionText label={s.label} detail={s.sources.join(", ")} />
        </Option>
      ))}
    </Combobox>
  );
}
