// Settings page tunables (General, Detection, Retention): saved in the database, they win
// over config.yaml until reset, and apply without a restart (server: runtime_settings.py).
import {
  Button,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Subtitle2,
  Switch,
} from "@fluentui/react-components";
import { ArrowUndo16Regular, Save20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type RuntimeSetting, type RuntimeSettings } from "../api";
import { fileSize } from "../format";
import { useI18n, type MessageKey } from "../i18n";
import { Fact, Facts, QueryState, Tag } from "./ui";

type Kind = "number" | "bool" | "url";

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
const asText = (v: RuntimeSetting["value"]) => (v === null ? "" : String(v));

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
  const byKey = new Map(data.settings.map((s) => [s.key, s]));
  const keys = groups.flatMap((g) => g.keys).filter((k) => byKey.has(k));
  const [draft, setDraft] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(
      keys.map((k) => {
        const v = byKey.get(k)!.value;
        return [k, typeof v === "boolean" ? v : asText(v)];
      }),
    ),
  );
  const save = useMutation({
    mutationFn: api.setRuntimeSettings,
    onSuccess: (next) => client.setQueryData(["runtimeSettings"], next),
  });
  const original = (k: string) => {
    const v = byKey.get(k)!.value;
    return typeof v === "boolean" ? v : asText(v);
  };
  const changed = keys.filter((k) => draft[k] !== original(k));
  const parse = (k: string): unknown => {
    const d = draft[k] ?? "";
    if (typeof d === "boolean") return d;
    if (d.trim() === "") return null;
    return META[k]?.kind === "url" ? d.trim() : Number(d);
  };

  return (
    <form
      className="form-stack runtime-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ values: Object.fromEntries(changed.map((k) => [k, parse(k)])) });
      }}
    >
      {groups.map((g) => (
        <section key={g.keys.join()} className="form-stack">
          {g.title && (
            <Subtitle2 as="h2" className="panel-heading">
              {t(g.title)}
            </Subtitle2>
          )}
          {g.keys.map((k) => {
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
                        appearance="transparent"
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
                    min={s.ge ?? s.gt}
                    max={s.le ?? s.lt}
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
          disabled={changed.length === 0 || save.isPending}
        >
          {t("common.save")}
        </Button>
      </div>
    </form>
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
  return meta.unit ? `${v} ${t(meta.unit)}` : String(v);
}

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
