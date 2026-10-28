import { Select } from "@fluentui/react-components";

import { useI18n } from "../i18n";
import { DateTimeInput } from "./ui";

/** Quick periods back from now; "custom" uses the From and To fields. */
const PERIODS = ["24h", "7d", "30d", "custom"] as const;
const PERIOD_HOURS: Record<string, number> = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 };

/** The URL keys of a period filter: the period, and From/To (local time) when custom. */
export const PERIOD_KEYS = ["period", "from", "to"] as const;

type PeriodValues = Record<(typeof PERIOD_KEYS)[number], string>;

// datetime-local values ("YYYY-MM-DDTHH:MM") are read as local time.
const iso = (local: string) => (local ? new Date(local).toISOString() : undefined);

/** A period filter as API bounds (UTC ISO). Relative periods count back from when it is
 *  called, so call it in the query function: each refetch moves along. */
export function periodBounds(f: PeriodValues): { since?: string; until?: string } {
  const hours = PERIOD_HOURS[f.period];
  if (hours) return { since: new Date(Date.now() - hours * 3_600_000).toISOString() };
  if (f.period !== "custom") return {};
  return { since: iso(f.from), until: iso(f.to) };
}

/** Any time, the last day/week/month, or a custom From–To (local time). */
export function PeriodFilter({
  value,
  onChange,
}: {
  value: PeriodValues;
  onChange: (key: (typeof PERIOD_KEYS)[number], value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <Select
        value={value.period}
        aria-label={t("filter.period")}
        onChange={(_, d) => onChange("period", d.value)}
      >
        <option value="">{t("filter.anyTime")}</option>
        {PERIODS.map((p) => (
          <option key={p} value={p}>
            {t(`filter.period.${p}`)}
          </option>
        ))}
      </Select>
      {value.period === "custom" && (
        <span className="period-range">
          <DateTimeInput
            type="datetime-local"
            value={value.from}
            placeholder={t("filter.from")}
            aria-label={t("filter.from")}
            onChange={(v) => onChange("from", v)}
          />
          <DateTimeInput
            type="datetime-local"
            value={value.to}
            placeholder={t("filter.to")}
            aria-label={t("filter.to")}
            onChange={(v) => onChange("to", v)}
          />
        </span>
      )}
    </>
  );
}

/** Recorded in daylight or at night (the camera's IR mode). */
export function LightSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <Select value={value} aria-label={t("col.light")} onChange={(_, d) => onChange(d.value)}>
      <option value="">{t("filter.anyLight")}</option>
      <option value="day">{t("filter.day")}</option>
      <option value="night">{t("filter.night")}</option>
    </Select>
  );
}

const FEEDBACK = ["correct", "wrong", "none"] as const;

/** A light filter from the URL for the API (anything else: no filter). */
export const asLight = (value: string): "day" | "night" | undefined =>
  value === "day" || value === "night" ? value : undefined;

/** A feedback filter from the URL for the API (anything else: no filter). */
export const asFeedback = (value: string) => FEEDBACK.find((f) => f === value);

/** Marked correct, wrong, or not yet. */
export function FeedbackSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <Select value={value} aria-label={t("filter.feedback")} onChange={(_, d) => onChange(d.value)}>
      <option value="">{t("filter.anyFeedback")}</option>
      <option value="correct">{t("filter.correct")}</option>
      <option value="wrong">{t("filter.wrong")}</option>
      <option value="none">{t("filter.unmarked")}</option>
    </Select>
  );
}
