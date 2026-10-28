import { Field, Input } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, type NotificationRules, type NotifyTrigger } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { LabelPicker, useLabelSuggestions, useSuggestionList } from "./LabelPicker";
import { useProfiles } from "./ProfileSelect";
import { Checkbox, DateTimeInput } from "./ui";

export const TRIGGERS: NotifyTrigger[] = ["event.new", "event.clip"];
export const TRIGGER_LABELS: Record<NotifyTrigger, MessageKey> = {
  "event.new": "notify.on.new",
  "event.clip": "notify.on.clip",
};

/** The rules as the form edits them (numbers as typed, quiet hours in two parts). */
export interface RulesDraft {
  when: NotifyTrigger[];
  cameras: string[] | null;
  profiles: string[] | null;
  labels: string[];
  minConf: string;
  minAgreement: string;
  quietFrom: string;
  quietTo: string;
  cooldown: string;
}

export function draftFromRules(r: Partial<NotificationRules> | null | undefined): RulesDraft {
  return {
    when: r?.when ?? ["event.new"],
    cameras: r?.cameras ?? null,
    profiles: r?.profiles ?? null,
    labels: r?.labels ?? [],
    minConf: String(Math.round((r?.min_confidence ?? 0) * 100)),
    minAgreement: String(r?.min_agreement ?? 1),
    quietFrom: r?.quiet_hours?.split("-")[0] ?? "",
    quietTo: r?.quiet_hours?.split("-")[1] ?? "",
    cooldown: String(r?.cooldown_minutes ?? 0),
  };
}

export function rulesFromDraft(d: RulesDraft): Omit<NotificationRules, "enabled"> {
  return {
    when: d.when,
    cameras: d.cameras,
    profiles: d.profiles,
    labels: d.labels.length ? d.labels : null,
    min_confidence: Math.min(Math.max(Number(d.minConf) / 100, 0), 1),
    min_agreement: Math.min(Math.max(Math.round(Number(d.minAgreement)) || 1, 1), 10),
    quiet_hours: d.quietFrom && d.quietTo ? `${d.quietFrom}-${d.quietTo}` : null,
    cooldown_minutes: Math.max(Number(d.cooldown) || 0, 0),
  };
}

/** Add or remove `value` from a filter list; null means "all". */
function toggle<T>(list: T[] | null, all: T[], value: T, checked: boolean): T[] | null {
  const next = checked ? [...(list ?? []), value] : (list ?? all).filter((x) => x !== value);
  return next.length === 0 || next.length === all.length ? null : next;
}

/**
 * Which events to notify about: when, cameras, profiles, labels, how sure, quiet hours and
 * cooldown. For a shared channel and for a user's own notifications alike.
 */
export function NotificationRulesFields({
  value: d,
  onChange,
  quietHint,
}: {
  value: RulesDraft;
  onChange: (next: RulesDraft) => void;
  /** Says which time zone quiet hours are in. */
  quietHint?: string;
}) {
  const { t } = useI18n();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const profiles = useProfiles();
  const labelData = useLabelSuggestions();
  const profileTargets = useMemo(
    () => ({
      labels: [...new Set((profiles.data ?? []).flatMap((p) => p.targets))],
      source: t("labels.profileTarget"),
    }),
    [profiles.data, t],
  );
  const labelSuggestions = useSuggestionList(labelData.data, profileTargets);
  const camIds = (cameras.data ?? []).map((c) => c.id);
  const profIds = (profiles.data ?? []).map((p) => p.id);
  const set = (patch: Partial<RulesDraft>) => onChange({ ...d, ...patch });

  return (
    <>
      <Field label={t("notify.when")}>
        <div className="row wrap">
          {TRIGGERS.map((tr) => (
            <Checkbox
              key={tr}
              label={t(TRIGGER_LABELS[tr])}
              checked={d.when.includes(tr)}
              onChange={(_, e) =>
                set({ when: e.checked ? [...d.when, tr] : d.when.filter((x) => x !== tr) })
              }
            />
          ))}
        </div>
      </Field>
      <Field label={t("nav.cameras")} hint={t("notify.allHint")}>
        <div className="row wrap">
          {(cameras.data ?? []).map((c) => (
            <Checkbox
              key={c.id}
              label={c.name}
              checked={d.cameras === null || d.cameras.includes(c.id)}
              onChange={(_, e) =>
                set({ cameras: toggle(d.cameras, camIds, c.id, e.checked === true) })
              }
            />
          ))}
        </div>
      </Field>
      {profIds.length > 1 && (
        <Field label={t("profiles.title")}>
          <div className="row wrap">
            {(profiles.data ?? []).map((p) => (
              <Checkbox
                key={p.id}
                label={p.display_name}
                checked={d.profiles === null || d.profiles.includes(p.id)}
                onChange={(_, e) =>
                  set({ profiles: toggle(d.profiles, profIds, p.id, e.checked === true) })
                }
              />
            ))}
          </div>
        </Field>
      )}
      <Field label={t("notify.labels")} hint={t("notify.labelsHint")}>
        <LabelPicker
          value={d.labels}
          onChange={(labels) => set({ labels })}
          suggestions={labelSuggestions}
          placeholder="fox"
          ariaLabel={t("notify.labels")}
        />
      </Field>
      <Field label={t("notify.notifyFrom")} hint={t("notify.notifyFromHint")}>
        <Input
          type="number"
          min={0}
          max={100}
          value={d.minConf}
          contentAfter="%"
          onChange={(_, e) => set({ minConf: e.value })}
        />
      </Field>
      <Field label={t("notify.minAgreement")} hint={t("notify.minAgreementHint")}>
        <Input
          type="number"
          min={1}
          max={10}
          value={d.minAgreement}
          onChange={(_, e) => set({ minAgreement: e.value })}
        />
      </Field>
      <Field label={t("notify.quietHours")} hint={quietHint ?? t("notify.quietHint")}>
        <div className="row wrap">
          <DateTimeInput
            type="time"
            value={d.quietFrom}
            placeholder={t("notify.quietFrom")}
            aria-label={t("notify.quietFrom")}
            onChange={(quietFrom) => set({ quietFrom })}
          />
          <span>–</span>
          <DateTimeInput
            type="time"
            value={d.quietTo}
            placeholder={t("notify.quietTo")}
            aria-label={t("notify.quietTo")}
            onChange={(quietTo) => set({ quietTo })}
          />
        </div>
      </Field>
      <Field label={t("notify.cooldownMinutes")} hint={t("notify.cooldownHint")}>
        <Input
          type="number"
          min={0}
          value={d.cooldown}
          onChange={(_, e) => set({ cooldown: e.value })}
        />
      </Field>
    </>
  );
}
