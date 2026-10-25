import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Switch,
  Text,
} from "@fluentui/react-components";
import {
  Add20Regular,
  Delete20Regular,
  Dismiss20Regular,
  Edit20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Profile, type ProfileInput } from "../api";
import {
  LabelCombobox,
  LabelPicker,
  TagsPicker,
  useLabelSuggestions,
  useSuggestionList,
} from "../components/LabelPicker";
import { useProfiles } from "../components/ProfileSelect";
import {
  DetectorsEditor,
  fromRows,
  rowsValid,
  toRows,
  type DetectorRow,
} from "../components/DetectorsEditor";
import { profileWarnings, type ProfileWarning } from "../labelWarnings";

import { ConfigTag, Empty, Fact, Facts, InfoBanner, QueryState, Tag } from "../components/ui";
import { percent } from "../format";
import { ConfirmPopover } from "../components/ConfirmPopover";
import { useI18n } from "../i18n";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";

type AliasRow = { from: string; to: string };

/** Complete rows only, as {"red fox": "fox"}. */
const aliasMap = (rows: AliasRow[]): Record<string, string> =>
  Object.fromEntries(
    rows.filter((r) => r.from.trim() && r.to.trim()).map((r) => [r.from.trim(), r.to.trim()]),
  );

/** Detection profiles: Cameras → Detection profiles (/cameras/profiles). */
export function ProfilesSection() {
  const { t, rich } = useI18n();
  const profiles = useProfiles();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [editing, setEditing] = useState<Profile | null>(null);
  const client = useQueryClient();
  const remove = (p: Profile) =>
    api.deleteProfile(p.id).then(() => {
      void client.invalidateQueries({ queryKey: ["profiles"] });
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["queue"] });
    });
  const cameraName = (id: string) => cameras.data?.find((c) => c.id === id)?.name ?? id;

  return (
    <section id="profiles">
      {/* "Add profile" is in the Cameras page's toolbar for this tab. */}
      <InfoBanner id="profiles">
        {rich("profiles.intro", { file: <code>config.yaml</code> })}
      </InfoBanner>
      <InfoBanner id="profiles-match">{t("profiles.matchIntro")}</InfoBanner>
      <QueryState error={profiles.error} loading={profiles.isPending} />
      {profiles.data?.length === 0 && <Empty>{t("profiles.none")}</Empty>}
      <div className="cards">
        {profiles.data?.map((p) => (
          <ProfileCard
            key={p.id}
            profile={p}
            cameraName={cameraName}
            onEdit={() => setEditing(p)}
            onRemove={() => remove(p)}
          />
        ))}
      </div>
      {editing && <ProfileDialog profile={editing} onClose={() => setEditing(null)} />}
    </section>
  );
}

function ProfileCard({
  profile: p,
  cameraName,
  onEdit,
  onRemove,
}: {
  profile: Profile;
  cameraName: (id: string) => string;
  onEdit: () => void;
  onRemove: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const editable = p.managed === "ui";
  const stuck = p.enabled && p.cameras.length > 0 && p.workers_online.length === 0;
  const aliases = Object.entries(p.aliases);

  return (
    <Card>
      <CardHeader
        header={
          <span className="row">
            <Text weight="semibold">{p.display_name}</Text>
            <Caption1 className="muted">{p.id}</Caption1>
          </span>
        }
        description={
          <span className="row wrap tags">
            {!editable && <ConfigTag />}
            {p.default && <Tag title={t("profiles.defaultHint")}>{t("profiles.default")}</Tag>}
            {!p.enabled && <Tag kind="warn">{t("profiles.disabled")}</Tag>}
          </span>
        }
      />
      <Facts>
        <Fact label={t("profiles.targets")}>
          <span className="row wrap tags">
            {p.targets.map((x) => (
              <Tag key={x} kind="target">
                {x}
              </Tag>
            ))}
          </span>
        </Fact>
        {aliases.length > 0 && (
          <>
            <Fact label={t("profiles.aliases")}>
              <span className="chips">
                {aliases.map(([from, to]) => (
                  <span key={from}>
                    {from} → {to}
                  </span>
                ))}
              </span>
            </Fact>
          </>
        )}
        <Fact label={t("profiles.matchFromShort")}>
          {percent(p.min_confidence)}
          {p.detectors.some((d) => d.min_confidence !== null) && (
            <span className="muted">
              {" ("}
              {p.detectors
                .filter((d) => d.min_confidence !== null)
                .map((d) => `${d.display_name} ${percent(d.min_confidence!)}`)
                .join(", ")}
              )
            </span>
          )}
        </Fact>
        <Fact label={t("profiles.requires")}>
          {p.requires.length ? (
            <span className="row wrap tags">
              {p.requires.map((x) => (
                <Tag key={x}>{x}</Tag>
              ))}
            </span>
          ) : (
            <span className="muted">{t("profiles.anyWorker")}</span>
          )}
        </Fact>
        <Fact label={t("detectors.title")}>
          <ul className="detector-list">
            {p.detectors.map((d) => (
              <li key={d.id}>
                <Text weight="semibold">{d.display_name}</Text>{" "}
                {d.requires.map((x) => (
                  <Tag key={x}>{x}</Tag>
                ))}{" "}
                <Caption1 className="muted">
                  {[
                    d.run === "on_hit" &&
                      t(d.confirm ? "detectors.confirmsAfter" : "detectors.onHitAfter", {
                        name:
                          p.detectors.find((o) => o.id === d.after)?.display_name ?? d.after ?? "",
                      }),
                    d.fps !== null && `${d.fps} fps`,
                    d.workers_online.length
                      ? d.workers_online.join(", ")
                      : t("detectors.noWorkers"),
                  ]
                    .filter(Boolean)
                    .join(" – ")}
                </Caption1>
              </li>
            ))}
          </ul>
        </Fact>
        <Fact label={t("profiles.fps")}>
          {p.fps ? `${p.fps} fps` : <span className="muted">{t("profiles.allFrames")}</span>}
        </Fact>
        <Fact label={t("profiles.stationary")}>
          {p.ignore_stationary_minutes ? (
            t("notify.minutes", { n: p.ignore_stationary_minutes })
          ) : (
            <span className="muted">{t("profiles.stationaryOff")}</span>
          )}
        </Fact>
        {p.events_days !== null && (
          <>
            <Fact label={t("profiles.keepEvents")}>
              {p.events_days ? t("profiles.days", { n: p.events_days }) : t("profiles.forever")}
            </Fact>
          </>
        )}
        <Fact label={t("nav.cameras")}>
          {p.cameras.length ? (
            <span className="chips">
              {p.cameras.map((id) => (
                <Tag key={id}>{cameraName(id)}</Tag>
              ))}
            </span>
          ) : (
            <span className="muted">{t("profiles.noCameras")}</span>
          )}
        </Fact>
        <Fact label={t("profiles.workers")}>
          {p.workers_online.length ? (
            <span className="chips">
              {p.workers_online.map((w) => (
                <span key={w}>{w}</span>
              ))}
            </span>
          ) : (
            <span className="muted">{t("profiles.noWorkers")}</span>
          )}
        </Fact>
        <Fact label={t("profiles.waiting")}>{p.waiting_jobs}</Fact>
      </Facts>
      {stuck && (
        <MessageBar intent="warning">
          <MessageBarBody>{t("profiles.stuck")}</MessageBarBody>
        </MessageBar>
      )}
      {editable && (
        <CardFooter className="wrap">
          <Button size="small" icon={<Edit20Regular />} onClick={onEdit}>
            {t("common.edit")}
          </Button>
          <ConfirmPopover
            title={t("profiles.removeTitle", { name: p.display_name })}
            body={t("profiles.removeBody")}
            onConfirm={onRemove}
          >
            <Button size="small" icon={<Delete20Regular />}>
              {t("common.remove")}
            </Button>
          </ConfirmPopover>
        </CardFooter>
      )}
    </Card>
  );
}

export function ProfileDialog({
  profile,
  onClose,
  onSaved,
}: {
  profile: Profile | null;
  onClose: () => void;
  /** After a successful save (before closing). */
  onSaved?: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [id, setId] = useState(profile?.id ?? "");
  const [name, setName] = useState(profile?.name ?? "");
  const [targets, setTargets] = useState<string[]>(profile?.targets ?? []);
  const [aliases, setAliases] = useState<AliasRow[]>(() =>
    Object.entries(profile?.aliases ?? {}).map(([from, to]) => ({ from, to })),
  );
  const labels = useLabelSuggestions();
  const suggestions = useSuggestionList(labels.data);
  const [minConf, setMinConf] = useState(
    String(Math.round((profile?.min_confidence ?? 0.5) * 100)),
  );
  const [requires, setRequires] = useState<string[]>(profile?.requires ?? []);
  const [detectors, setDetectors] = useState<DetectorRow[]>(() => toRows(profile?.detectors));
  const [fps, setFps] = useState(profile?.fps?.toString() ?? "");
  const [eventsDays, setEventsDays] = useState(profile?.events_days?.toString() ?? "");
  const [stationary, setStationary] = useState(
    (profile?.ignore_stationary_minutes ?? 10).toString(),
  );
  const [isDefault, setDefault] = useState(profile?.default ?? false);
  const [enabled, setEnabled] = useState(profile?.enabled ?? true);

  const body = (): ProfileInput => ({
    id: id.trim(),
    name: name.trim() || null,
    targets: targets.map((s) => s.trim()).filter(Boolean),
    aliases: aliasMap(aliases),
    min_confidence: Math.min(Math.max(Number(minConf) / 100, 0), 1),
    requires,
    detectors: fromRows(detectors),
    fps: fps ? Number(fps) : null,
    default: isDefault,
    events_days: eventsDays === "" ? null : Number(eventsDays),
    ignore_stationary_minutes: stationary === "" ? 10 : Number(stationary),
    enabled,
  });
  const save = useMutation({
    mutationFn: () => (profile ? api.updateProfile(body()) : api.createProfile(body())),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["profiles"] });
      void client.invalidateQueries({ queryKey: ["cameras"] });
      onSaved?.();
      onClose();
    },
  });
  const matching = (labels.data?.workers ?? []).filter((w) =>
    requires.every((tag) => w.tags.includes(tag)),
  );
  // Per detector (each runs on its own workers), the same warning once.
  const warnings = [
    ...new Map(
      detectors
        .flatMap((d) =>
          profileWarnings(targets, aliasMap(aliases), [...requires, ...d.requires], labels.data),
        )
        .map((w): [string, ProfileWarning] => [`${w.kind}:${w.labels.join()}`, w]),
    ).values(),
  ];
  const setAlias = (i: number, row: Partial<AliasRow>) =>
    setAliases((rows) => rows.map((r, j) => (j === i ? { ...r, ...row } : r)));

  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface className="add-camera-dialog">
        <form
          // The browser's own check would count the picker's empty text box as a missing
          // target; Save is disabled until there is an id and a target instead.
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <DialogBody>
            <DialogTitle>
              {profile
                ? t("profiles.editTitle", { name: profile.display_name })
                : t("profiles.add")}
            </DialogTitle>
            <DialogContent className="form-stack">
              <Field label={t("profiles.name")} hint={t("profiles.nameHint")}>
                <Input value={name} onChange={(_, d) => setName(d.value)} />
              </Field>
              {!profile && (
                <Field label={t("profiles.id")} hint={t("profiles.idHint")} required>
                  <Input
                    value={id}
                    onChange={(_, d) => setId(d.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))}
                  />
                </Field>
              )}
              <Field label={t("profiles.targets")} hint={t("profiles.targetsHint")} required>
                <LabelPicker
                  value={targets}
                  onChange={setTargets}
                  suggestions={suggestions}
                  placeholder={t("labels.targetsPlaceholder")}
                  ariaLabel={t("profiles.targets")}
                />
              </Field>
              {warnings.map((w) => (
                <MessageBar key={`${w.kind}:${w.labels.join()}`} intent="warning">
                  <MessageBarBody>
                    {w.kind === "unreportable"
                      ? t("labels.warnUnreportable", {
                          labels: w.labels.join(", "),
                          detectors: w.detectors.join(", "),
                        })
                      : t("labels.warnSynonyms", { labels: w.labels.join(", ") })}
                  </MessageBarBody>
                </MessageBar>
              ))}
              <Field label={t("profiles.aliases")} hint={t("profiles.aliasesHint")}>
                <div className="alias-rows">
                  {aliases.map((row, i) => (
                    // oxlint-disable-next-line react/no-array-index-key -- rows have no id
                    <div key={i} className="alias-row">
                      <LabelCombobox
                        value={row.from}
                        onChange={(from) => setAlias(i, { from })}
                        suggestions={suggestions}
                        placeholder="red fox"
                        ariaLabel={t("labels.modelLabel")}
                      />
                      <span aria-hidden className="muted">
                        →
                      </span>
                      <Input
                        value={row.to}
                        placeholder="fox"
                        aria-label={t("labels.yourLabel")}
                        onChange={(_, d) => setAlias(i, { to: d.value })}
                      />
                      <Button
                        appearance="subtle"
                        icon={<Dismiss20Regular />}
                        aria-label={t("common.remove")}
                        onClick={() => setAliases((rows) => rows.filter((_, j) => j !== i))}
                      />
                    </div>
                  ))}
                  <Button
                    size="small"
                    icon={<Add20Regular />}
                    className="alias-add"
                    onClick={() => setAliases((rows) => [...rows, { from: "", to: "" }])}
                  >
                    {t("labels.addAlias")}
                  </Button>
                </div>
              </Field>
              <Field label={t("profiles.matchFrom")} hint={t("profiles.matchFromHint")}>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={minConf}
                  contentAfter="%"
                  onChange={(_, d) => setMinConf(d.value)}
                />
              </Field>
              <Field
                label={t("profiles.requires")}
                hint={
                  <>
                    {t("profiles.requiresHint")}{" "}
                    {!!labels.data?.workers.length &&
                      (matching.length
                        ? t(
                            requires.length ? "profiles.requiresMatching" : "profiles.requiresAny",
                            {
                              workers: matching.map((w) => w.id).join(", "),
                            },
                          )
                        : t("profiles.requiresNoMatch"))}
                  </>
                }
              >
                <TagsPicker
                  value={requires}
                  onChange={setRequires}
                  placeholder="gpu"
                  ariaLabel={t("profiles.requires")}
                />
              </Field>
              <Field label={t("detectors.title")} hint={t("detectors.hint")}>
                <DetectorsEditor
                  rows={detectors}
                  onChange={setDetectors}
                  common={requires}
                  workers={labels.data?.workers ?? []}
                  profileMinConf={minConf}
                />
              </Field>
              <Field label={t("profiles.fps")} hint={t("profiles.fpsHint")}>
                <Input
                  type="number"
                  min={0.1}
                  max={30}
                  step={0.1}
                  value={fps}
                  placeholder={t("profiles.allFrames")}
                  onChange={(_, d) => setFps(d.value)}
                />
              </Field>
              <Field label={t("profiles.keepEventsDays")} hint={t("profiles.keepEventsHint")}>
                <Input
                  type="number"
                  min={0}
                  value={eventsDays}
                  placeholder={t("camera.default")}
                  onChange={(_, d) => setEventsDays(d.value)}
                />
              </Field>
              <Field label={t("profiles.stationary")} hint={t("profiles.stationaryHint")}>
                <Input
                  type="number"
                  min={0}
                  max={1440}
                  value={stationary}
                  onChange={(_, d) => setStationary(d.value)}
                />
              </Field>
              <Switch
                label={t("profiles.defaultSwitch")}
                checked={isDefault}
                onChange={(_, d) => setDefault(d.checked)}
              />
              <Switch
                label={t("profiles.enabled")}
                checked={enabled}
                onChange={(_, d) => setEnabled(d.checked)}
              />
              {save.error && (
                <MessageBar intent="error">
                  <MessageBarBody>{save.error.message}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button
                type="submit"
                appearance="primary"
                disabled={!id.trim() || !targets.length || !rowsValid(detectors) || save.isPending}
              >
                {t("common.save")}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}
