// Settings → Retention: how long recordings and events are kept and the disk cap. Saved in
// the database; a value set here wins over config.yaml's `retention` until reset.
import { Button, Field, Input, MessageBar, MessageBarBody } from "@fluentui/react-components";
import { ArrowUndo16Regular, Save20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type RetentionKey, type RetentionSettings } from "../api";
import { fileSize } from "../format";
import { useI18n, type MessageKey } from "../i18n";
import { Fact, Facts, InfoBanner, QueryState, Tag } from "./ui";

const FIELDS: { key: RetentionKey; label: MessageKey; hint: MessageKey; unit: MessageKey }[] = [
  {
    key: "segments_days",
    label: "retention.segmentsDays",
    hint: "retention.segmentsDaysHint",
    unit: "retention.days",
  },
  {
    key: "events_days",
    label: "retention.eventsDays",
    hint: "retention.eventsDaysHint",
    unit: "retention.days",
  },
  {
    key: "unprocessed_max_days",
    label: "retention.unprocessedDays",
    hint: "retention.unprocessedDaysHint",
    unit: "retention.days",
  },
  {
    key: "max_disk_gb",
    label: "retention.maxDisk",
    hint: "retention.maxDiskHint",
    unit: "retention.gb",
  },
];

const shown = (v: number | null) => (v === null ? "" : String(v));

export function RetentionSection() {
  const { t, rich } = useI18n();
  const retention = useQuery({ queryKey: ["retention"], queryFn: api.retention });
  return (
    <>
      <InfoBanner id="retention">
        {rich("retention.intro", { file: <code>config.yaml</code> })}
      </InfoBanner>
      <QueryState error={retention.error} loading={retention.isPending} />
      {retention.data && (
        <Facts className="block retention-facts">
          <Fact label={t("dashboard.recordingsStored")}>
            {fileSize(retention.data.recordings_bytes)}
          </Fact>
          <Fact label={t("dashboard.diskFree")}>
            {t("retention.freeOf", {
              free: fileSize(retention.data.disk_free_bytes),
              total: fileSize(retention.data.disk_total_bytes),
            })}
          </Fact>
        </Facts>
      )}
      {retention.data && (
        <RetentionForm
          // A fresh form once saved values come back.
          key={JSON.stringify(retention.data.values)}
          data={retention.data}
        />
      )}
    </>
  );
}

function RetentionForm({ data }: { data: RetentionSettings }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [draft, setDraft] = useState(() =>
    Object.fromEntries(FIELDS.map((f) => [f.key, shown(data.values[f.key])])),
  );
  const save = useMutation({
    mutationFn: api.setRetention,
    onSuccess: (next) => client.setQueryData(["retention"], next),
  });
  const changed = FIELDS.filter((f) => draft[f.key] !== shown(data.values[f.key]));

  return (
    <form
      className="form-stack retention-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(
          Object.fromEntries(
            changed.map((f) => [f.key, draft[f.key] === "" ? null : Number(draft[f.key])]),
          ),
        );
      }}
    >
      {FIELDS.map((f) => {
        const fromUi = data.from_ui.includes(f.key);
        return (
          <Field
            key={f.key}
            label={
              <span className="row">
                {t(f.label)}
                {fromUi && <Tag kind="info">{t("retention.setHere")}</Tag>}
              </span>
            }
            hint={
              <>
                {t(f.hint)}{" "}
                {fromUi && (
                  <Button
                    size="small"
                    appearance="transparent"
                    icon={<ArrowUndo16Regular />}
                    disabled={save.isPending}
                    onClick={() => save.mutate({ [f.key]: "config" })}
                  >
                    {t("retention.useConfig", {
                      value:
                        data.config[f.key] === null
                          ? t("retention.noCap")
                          : String(data.config[f.key]),
                    })}
                  </Button>
                )}
              </>
            }
          >
            <Input
              type="number"
              min={0}
              step="any"
              value={draft[f.key]}
              placeholder={f.key === "max_disk_gb" ? t("retention.noCap") : undefined}
              contentAfter={t(f.unit)}
              onChange={(_, d) => setDraft((v) => ({ ...v, [f.key]: d.value }))}
            />
          </Field>
        );
      })}
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
