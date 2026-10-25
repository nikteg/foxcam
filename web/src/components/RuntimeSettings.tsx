// Settings page tunables (General, Detection, Retention): saved in the database, they win
// over config.yaml until reset, and apply without a restart (server: runtime_settings.py).
import {
  Button,
  Field,
  Divider,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Subtitle2,
  Switch,
} from "@fluentui/react-components";
import { ArrowUndo16Regular, Save20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import { api, type RuntimeSetting, type RuntimeSettings } from "../api";
import { fileSize } from "../format";
import { useI18n, type MessageKey } from "../i18n";
import { useRecordingProfiles, useRecordingSummary } from "../recordingProfiles";
import { Fact, Facts, QueryState, Tag } from "./ui";

/** "percent": stored 0–1, entered and shown as 0–100 %. */
type Kind = "number" | "bool" | "url" | "percent";

/** Not a runtime setting: the default recording profile (its own endpoint, saved with the
 * same Save button). Put this key in a group to show it there. */
export const DEFAULT_RECORDING = "recording_profile_default";

/** Label, help text (the trade-off) and unit of each setting the UI shows. */
const META: Record<
  string,
  { label: MessageKey; hint: MessageKey; unit?: MessageKey; kind?: Kind }
> = {
  public_url: { label: "runtime.publicUrl", hint: "runtime.publicUrlHint", kind: "url" },
  segment_seconds: {
    label: "runtime.segmentSeconds",
    hint: "runtime.segmentSecondsHint",
    unit: "runtime.unit.seconds",
  },
  "compaction.enabled": {
    label: "runtime.compactionEnabled",
    hint: "runtime.compactionEnabledHint",
    kind: "bool",
  },
  "compaction.delay_minutes": {
    label: "runtime.compactionDelay",
    hint: "runtime.compactionDelayHint",
    unit: "runtime.unit.minutes",
  },
  "processing.sample_fps": {
    label: "runtime.sampleFps",
    hint: "runtime.sampleFpsHint",
    unit: "runtime.unit.fps",
  },
  "processing.max_width": {
    label: "runtime.maxWidth",
    hint: "runtime.maxWidthHint",
    unit: "runtime.unit.px",
  },
  "processing.concurrency": { label: "runtime.concurrency", hint: "runtime.concurrencyHint" },
  "processing.keep_non_targets_from": {
    label: "runtime.keepNonTargets",
    hint: "runtime.keepNonTargetsHint",
    unit: "runtime.unit.percent",
    kind: "percent",
  },
  "mediamtx.low_data.enabled": {
    label: "runtime.lowDataEnabled",
    hint: "runtime.lowDataEnabledHint",
    kind: "bool",
  },
  "mediamtx.low_data.height": {
    label: "runtime.lowDataHeight",
    hint: "runtime.lowDataHeightHint",
    unit: "runtime.unit.lines",
  },
  "mediamtx.low_data.fps": {
    label: "runtime.lowDataFps",
    hint: "runtime.lowDataFpsHint",
    unit: "runtime.unit.fps",
  },
  "mediamtx.low_data.bitrate_kbit": {
    label: "runtime.lowDataBitrate",
    hint: "runtime.lowDataBitrateHint",
    unit: "runtime.unit.kbit",
  },
  "processing.night_saturation_threshold": {
    label: "runtime.nightThreshold",
    hint: "runtime.nightThresholdHint",
  },
  "events.pre_roll_seconds": {
    label: "runtime.preRoll",
    hint: "runtime.preRollHint",
    unit: "runtime.unit.seconds",
  },
  "events.post_roll_seconds": {
    label: "runtime.postRoll",
    hint: "runtime.postRollHint",
    unit: "runtime.unit.seconds",
  },
  "events.merge_gap_seconds": {
    label: "runtime.mergeGap",
    hint: "runtime.mergeGapHint",
    unit: "runtime.unit.seconds",
  },
  "events.settle_seconds": {
    label: "runtime.settle",
    hint: "runtime.settleHint",
    unit: "runtime.unit.seconds",
  },
  "queue.lease_seconds": {
    label: "runtime.leaseSeconds",
    hint: "runtime.leaseSecondsHint",
    unit: "runtime.unit.seconds",
  },
  "queue.max_attempts": { label: "runtime.maxAttempts", hint: "runtime.maxAttemptsHint" },
  "queue.retry_backoff_seconds": {
    label: "runtime.retryBackoff",
    hint: "runtime.retryBackoffHint",
    unit: "runtime.unit.seconds",
  },
  "retention.segments_days": {
    label: "retention.segmentsDays",
    hint: "retention.segmentsDaysHint",
    unit: "retention.days",
  },
  "retention.events_days": {
    label: "retention.eventsDays",
    hint: "retention.eventsDaysHint",
    unit: "retention.days",
  },
  "retention.unprocessed_max_days": {
    label: "retention.unprocessedDays",
    hint: "retention.unprocessedDaysHint",
    unit: "retention.days",
  },
  "retention.max_disk_gb": {
    label: "retention.maxDisk",
    hint: "retention.maxDiskHint",
    unit: "retention.gb",
  },
};

export interface SettingsGroup {
  title?: MessageKey;
  keys: string[];
}

/** The text in an input for a value (booleans use a switch). */
const asText = (v: RuntimeSetting["value"], kind?: Kind) =>
  v === null ? "" : kind === "percent" ? String(toPercent(v as number)) : String(v);
const toPercent = (v: number) => Math.round(v * 10000) / 100;

/** One tab's settings, in groups, with one Save button. */
export function RuntimeSettingsSection({
  groups,
  showDisk = false,
}: {
  groups: SettingsGroup[];
  /** Disk use above the form (the Retention tab). */
  showDisk?: boolean;
}) {
  const query = useQuery({ queryKey: ["runtimeSettings"], queryFn: api.runtimeSettings });
  return (
    <>
      <QueryState error={query.error} loading={query.isPending} />
      {query.data && showDisk && <DiskUse data={query.data} />}
      {query.data && (
        <SettingsForm
          // A fresh form once saved values come back.
          key={JSON.stringify(query.data.settings.map((s) => [s.value, s.source]))}
          groups={groups}
          data={query.data}
        />
      )}
    </>
  );
}

function SettingsForm({ groups, data }: { groups: SettingsGroup[]; data: RuntimeSettings }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const recording = useDefaultRecording(groups.some((g) => g.keys.includes(DEFAULT_RECORDING)));
  const byKey = new Map(data.settings.map((s) => [s.key, s]));
  const keys = groups.flatMap((g) => g.keys).filter((k) => byKey.has(k));
  const [draft, setDraft] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(
      keys.map((k) => {
        const v = byKey.get(k)!.value;
        return [k, typeof v === "boolean" ? v : asText(v, META[k]?.kind)];
      }),
    ),
  );
  const save = useMutation({
    mutationFn: async (change: { values?: Record<string, unknown>; reset?: string[] }) => {
      if (!change.reset && recording.changed) {
        await api.setDefaultRecordingProfile(recording.draft);
      }
      const runtime = change.reset?.length || Object.keys(change.values ?? {}).length;
      return runtime ? api.setRuntimeSettings(change) : null;
    },
    onSuccess: (next) => {
      if (next) client.setQueryData(["runtimeSettings"], next);
      if (recording.changed) recording.saved();
    },
  });
  const original = (k: string) => {
    const v = byKey.get(k)!.value;
    return typeof v === "boolean" ? v : asText(v, META[k]?.kind);
  };
  const changed = keys.filter((k) => draft[k] !== original(k));
  const parse = (k: string): unknown => {
    const d = draft[k] ?? "";
    if (typeof d === "boolean") return d;
    if (d.trim() === "") return null;
    const kind = META[k]?.kind;
    return kind === "url" ? d.trim() : kind === "percent" ? Number(d) / 100 : Number(d);
  };

  return (
    <form
      className="form-stack runtime-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ values: Object.fromEntries(changed.map((k) => [k, parse(k)])) });
      }}
    >
      {groups
        .filter((g) => g.keys.some((k) => byKey.has(k) || k === DEFAULT_RECORDING))
        .map((g, i) => (
          <Fragment key={g.keys.join()}>
            {i > 0 && <Divider className="settings-divider" />}
            <section className="form-stack">
              {g.title && (
                <Subtitle2 as="h2" className="panel-heading">
                  {t(g.title)}
                </Subtitle2>
              )}
              {g.keys.map((k) => {
                if (k === DEFAULT_RECORDING) {
                  return <DefaultRecordingField key={k} state={recording} />;
                }
                const s = byKey.get(k);
                const meta = META[k];
                if (!s || !meta) return null;
                const value = draft[k]!;
                return (
                  <Field
                    key={k}
                    label={
                      <span className="row">
                        {t(meta.label)}
                        <SourceTag source={s.source} />
                      </span>
                    }
                    hint={
                      <>
                        {t(meta.hint)}
                        {s.source === "ui" && (
                          <Button
                            size="small"
                            className="reset-setting"
                            icon={<ArrowUndo16Regular />}
                            disabled={save.isPending}
                            onClick={() => save.mutate({ reset: [k] })}
                          >
                            {t(
                              s.config_source === "config"
                                ? "runtime.resetToConfig"
                                : "runtime.resetToDefault",
                              { value: shown(t, s.config_value, meta) },
                            )}
                          </Button>
                        )}
                      </>
                    }
                  >
                    {typeof value === "boolean" ? (
                      <Switch
                        checked={value}
                        onChange={(_, d) => setDraft((v) => ({ ...v, [k]: d.checked }))}
                      />
                    ) : (
                      <Input
                        type={meta.kind === "url" ? "url" : "number"}
                        min={scaled(s.ge ?? s.gt, meta.kind)}
                        max={scaled(s.le ?? s.lt, meta.kind)}
                        step="any"
                        value={value}
                        placeholder={
                          s.nullable
                            ? t(meta.kind === "url" ? "runtime.notSet" : "retention.noCap")
                            : undefined
                        }
                        contentAfter={meta.unit ? t(meta.unit) : undefined}
                        onChange={(_, d) => setDraft((v) => ({ ...v, [k]: d.value }))}
                      />
                    )}
                  </Field>
                );
              })}
            </section>
          </Fragment>
        ))}
      {save.error && (
        <MessageBar intent="error">
          <MessageBarBody>{save.error.message}</MessageBarBody>
        </MessageBar>
      )}
      <div className="row">
        <Button
          type="submit"
          appearance="primary"
          icon={<Save20Regular />}
          disabled={(changed.length === 0 && !recording.changed) || save.isPending}
        >
          {t("common.save")}
        </Button>
      </div>
    </form>
  );
}

type DefaultRecording = ReturnType<typeof useDefaultRecording>;

/** The default recording profile: the list, the current choice and its source, and the
 * unsaved choice in the form. */
function useDefaultRecording(enabled: boolean) {
  const client = useQueryClient();
  const profiles = useRecordingProfiles();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, enabled });
  const [choice, setChoice] = useState<string | null>(null);
  const list = enabled ? (profiles.data ?? []) : [];
  const current = list.find((p) => p.default);
  const draft = choice ?? current?.id ?? null;
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["recording-profiles"] });
    void client.invalidateQueries({ queryKey: ["cameras"] });
  };
  const reset = useMutation({
    mutationFn: () => api.setDefaultRecordingProfile(null),
    onSuccess: () => {
      setChoice(null);
      refresh();
    },
  });
  return {
    list,
    current,
    draft,
    choose: setChoice,
    changed: choice !== null && choice !== current?.id,
    /** config.yaml's profile, for the reset button. */
    fromConfig: list.find((p) => p.id === settings.data?.recording_profile_default),
    saved: () => {
      setChoice(null);
      refresh();
    },
    reset,
  };
}

function DefaultRecordingField({ state }: { state: DefaultRecording }) {
  const { t } = useI18n();
  const summary = useRecordingSummary();
  const chosen = state.list.find((p) => p.id === state.draft);
  if (!state.current) return null;
  const fromUi = state.current.default_source === "ui";
  return (
    <Field
      label={
        <span className="row">
          {t("runtime.recordingDefault")}
          <SourceTag source={fromUi ? "ui" : "config"} />
        </span>
      }
      hint={
        <>
          {chosen && <>{summary(chosen)}. </>}
          {t("runtime.recordingDefaultHint")}
          {fromUi && state.fromConfig && (
            <Button
              size="small"
              className="reset-setting"
              icon={<ArrowUndo16Regular />}
              disabled={state.reset.isPending}
              onClick={() => state.reset.mutate()}
            >
              {t("runtime.resetToConfig", { value: state.fromConfig.display_name })}
            </Button>
          )}
        </>
      }
    >
      <Select value={state.draft ?? ""} onChange={(_, d) => state.choose(d.value)}>
        {state.list.map((p) => (
          <option key={p.id} value={p.id}>
            {p.display_name}
          </option>
        ))}
      </Select>
    </Field>
  );
}

/** Where the value comes from. */
function SourceTag({ source }: { source: RuntimeSetting["source"] }) {
  const { t } = useI18n();
  if (source === "ui") return <Tag kind="info">{t("runtime.source.ui")}</Tag>;
  return <Tag>{t(source === "config" ? "runtime.source.config" : "runtime.source.default")}</Tag>;
}

function shown(
  t: ReturnType<typeof useI18n>["t"],
  v: RuntimeSetting["value"],
  meta: (typeof META)[string],
): string {
  if (v === null) return t(meta.kind === "url" ? "runtime.notSet" : "retention.noCap");
  if (typeof v === "boolean") return t(v ? "runtime.on" : "runtime.off");
  if (meta.kind === "percent") return `${toPercent(v as number)} %`;
  return meta.unit ? `${v} ${t(meta.unit)}` : String(v);
}

const scaled = (bound: number | undefined, kind?: Kind) =>
  bound !== undefined && kind === "percent" ? toPercent(bound) : bound;

/** Retention tab: disk use above the settings. */
function DiskUse({ data }: { data: RuntimeSettings }) {
  const { t } = useI18n();
  return (
    <Facts className="block runtime-facts">
      <Fact label={t("dashboard.recordingsStored")}>{fileSize(data.disk.recordings_bytes)}</Fact>
      <Fact label={t("dashboard.diskFree")}>
        {t("retention.freeOf", {
          free: fileSize(data.disk.disk_free_bytes),
          total: fileSize(data.disk.disk_total_bytes),
        })}
      </Fact>
    </Facts>
  );
}
