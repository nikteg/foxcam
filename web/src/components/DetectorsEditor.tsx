// The models (detectors) of a detection profile: each gets its own job per recording, run
// by workers with all its tags; their detections merge into the profile's events.
import { Button, Caption1, Field, Input, Select } from "@fluentui/react-components";
import { Checkbox } from "./ui";
import { Add20Regular, Dismiss20Regular } from "@fluentui/react-icons";

import type { DetectorInput, LabelSuggestions } from "../api";
import { useI18n } from "../i18n";
import { TagsPicker } from "./LabelPicker";

/** Editable row: numbers as typed (empty = the profile's value). */
export interface DetectorRow {
  id: string;
  name: string;
  requires: string[];
  minConf: string;
  fps: string;
  run: "always" | "on_hit";
  after: string;
  confirm: boolean;
}

const DEFAULT: DetectorInput = {
  id: "default",
  name: null,
  requires: [],
  min_confidence: null,
  fps: null,
  run: "always",
  after: null,
  confirm: false,
};

export const toRows = (detectors: DetectorInput[] | undefined): DetectorRow[] =>
  (detectors?.length ? detectors : [DEFAULT]).map((d) => ({
    id: d.id,
    name: d.name ?? "",
    requires: d.requires,
    minConf: d.min_confidence == null ? "" : String(Math.round(d.min_confidence * 100)),
    fps: d.fps == null ? "" : String(d.fps),
    run: d.run ?? "always",
    after: d.after ?? "",
    confirm: d.confirm ?? false,
  }));

export const fromRows = (rows: DetectorRow[]): DetectorInput[] =>
  rows.map((r) => ({
    id: r.id.trim(),
    name: r.name.trim() || null,
    requires: r.requires,
    min_confidence: r.minConf === "" ? null : Math.min(Math.max(Number(r.minConf) / 100, 0), 1),
    fps: r.fps === "" ? null : Number(r.fps),
    run: r.run,
    after: r.run === "on_hit" ? r.after.trim() || null : null,
    confirm: r.run === "on_hit" && r.confirm,
  }));

/** Detector ids: unique slugs; at least one runs always; cascades follow another one.
 * (The server also refuses circles.) */
export const rowsValid = (rows: DetectorRow[]) =>
  rows.length > 0 &&
  rows.every((r) => /^[a-z0-9][a-z0-9_-]*$/.test(r.id.trim())) &&
  new Set(rows.map((r) => r.id.trim())).size === rows.length &&
  rows.some((r) => r.run === "always") &&
  rows.every(
    (r) => r.run === "always" || rows.some((o) => o !== r && o.id.trim() === r.after.trim()),
  );

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9_-]/g, "-");

export function DetectorsEditor({
  rows,
  onChange,
  common,
  workers,
  profileMinConf,
}: {
  rows: DetectorRow[];
  onChange: (rows: DetectorRow[]) => void;
  /** The profile's own tags (every detector needs them too). */
  common: string[];
  workers: LabelSuggestions["workers"];
  profileMinConf: string;
}) {
  const { t } = useI18n();
  const set = (i: number, row: Partial<DetectorRow>) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...row } : r)));
  const free = () => {
    const taken = new Set(rows.map((r) => r.id));
    let n = rows.length + 1;
    while (taken.has(`model-${n}`)) n += 1;
    return `model-${n}`;
  };

  return (
    <div className="detector-rows">
      {rows.map((row, i) => {
        const need = [...common, ...row.requires];
        const able = workers.filter((w) => need.every((tag) => w.tags.includes(tag)));
        return (
          // oxlint-disable-next-line react/no-array-index-key -- ids are being edited
          <fieldset key={i} className="detector-row">
            <div className="detector-head">
              <Field label={t("detectors.id")} size="small">
                <Input
                  size="small"
                  value={row.id}
                  onChange={(_, d) => set(i, { id: slug(d.value) })}
                />
              </Field>
              <Field label={t("profiles.name")} size="small">
                <Input
                  size="small"
                  value={row.name}
                  placeholder={row.id}
                  onChange={(_, d) => set(i, { name: d.value })}
                />
              </Field>
              <Button
                appearance="subtle"
                icon={<Dismiss20Regular />}
                aria-label={t("common.remove")}
                disabled={rows.length === 1}
                onClick={() => onChange(rows.filter((_, j) => j !== i))}
              />
            </div>
            <Field label={t("detectors.requires")} size="small">
              <TagsPicker
                value={row.requires}
                onChange={(requires) => set(i, { requires })}
                placeholder={t("detectors.requiresPlaceholder")}
                ariaLabel={t("detectors.requires")}
              />
            </Field>
            <div className="detector-numbers">
              <Field label={t("profiles.minConfidencePercent")} size="small">
                <Input
                  size="small"
                  type="number"
                  min={0}
                  max={100}
                  value={row.minConf}
                  placeholder={t("detectors.profileValue", { value: profileMinConf })}
                  onChange={(_, d) => set(i, { minConf: d.value })}
                />
              </Field>
              <Field label={t("profiles.fps")} size="small">
                <Input
                  size="small"
                  type="number"
                  min={0.1}
                  max={30}
                  step={0.1}
                  value={row.fps}
                  placeholder={t("detectors.profiles")}
                  onChange={(_, d) => set(i, { fps: d.value })}
                />
              </Field>
            </div>
            {rows.length > 1 && (
              <div className="detector-numbers">
                <Field label={t("detectors.run")} size="small">
                  <Select
                    size="small"
                    value={row.run}
                    onChange={(_, d) =>
                      set(i, {
                        run: d.value as DetectorRow["run"],
                        after: row.after || rows.find((o) => o !== row)?.id || "",
                      })
                    }
                  >
                    <option value="always">{t("detectors.runAlways")}</option>
                    <option value="on_hit">{t("detectors.runOnHit")}</option>
                  </Select>
                </Field>
                {row.run === "on_hit" && (
                  <Field label={t("detectors.after")} size="small">
                    <Select
                      size="small"
                      value={row.after}
                      onChange={(_, d) => set(i, { after: d.value })}
                    >
                      {rows
                        .filter((o) => o !== row)
                        .map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.name || o.id}
                          </option>
                        ))}
                    </Select>
                  </Field>
                )}
              </div>
            )}
            {row.run === "on_hit" && rows.length > 1 && (
              <Checkbox
                label={t("detectors.confirm")}
                checked={row.confirm}
                onChange={(_, d) => set(i, { confirm: d.checked === true })}
              />
            )}
            {workers.length > 0 && (
              <Caption1 className="muted">
                {able.length
                  ? t("detectors.workers", { workers: able.map((w) => w.id).join(", ") })
                  : t("profiles.requiresNoMatch")}
              </Caption1>
            )}
          </fieldset>
        );
      })}
      <Button
        size="small"
        icon={<Add20Regular />}
        className="alias-add"
        onClick={() =>
          onChange([
            ...rows,
            {
              id: free(),
              name: "",
              requires: [],
              minConf: "",
              fps: "",
              run: "always",
              after: "",
              confirm: false,
            },
          ])
        }
      >
        {t("detectors.add")}
      </Button>
    </div>
  );
}
