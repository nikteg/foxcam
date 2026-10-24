import {
  Body1,
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Subtitle2,
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
  useLabelSuggestions,
  useSuggestionList,
} from "../components/LabelPicker";
import { useProfiles } from "../components/ProfileSelect";
import { profileWarnings } from "../labelWarnings";
import { Navigate } from "react-router";

import { Empty, QueryState, Tag } from "../components/ui";
import { percent } from "../format";
import { useI18n } from "../i18n";

const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

type AliasRow = { from: string; to: string };

/** Complete rows only, as {"red fox": "fox"}. */
const aliasMap = (rows: AliasRow[]): Record<string, string> =>
  Object.fromEntries(
    rows.filter((r) => r.from.trim() && r.to.trim()).map((r) => [r.from.trim(), r.to.trim()]),
  );

/** Detection profiles: shown on the Cameras page (anchor #profiles). */
export function ProfilesSection() {
  const { t, rich } = useI18n();
  const profiles = useProfiles();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [removing, setRemoving] = useState<Profile | null>(null);
  const cameraName = (id: string) => cameras.data?.find((c) => c.id === id)?.name ?? id;

  return (
    <section id="profiles">
      <Subtitle2 as="h2" className="section">
        {t("profiles.title")}
        <Button
          size="small"
          appearance="primary"
          icon={<Add20Regular />}
          className="section-action"
          onClick={() => setEditing("new")}
        >
          {t("profiles.add")}
        </Button>
      </Subtitle2>
      <p className="muted">{rich("profiles.intro", { file: <code>config.yaml</code> })}</p>
      <QueryState error={profiles.error} loading={profiles.isPending} />
      {profiles.data?.length === 0 && <Empty>{t("profiles.none")}</Empty>}
      <div className="cards">
        {profiles.data?.map((p) => (
          <ProfileCard
            key={p.id}
            profile={p}
            cameraName={cameraName}
            onEdit={() => setEditing(p)}
            onRemove={() => setRemoving(p)}
          />
        ))}
      </div>
      {editing && (
        <ProfileDialog
          profile={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {removing && <RemoveProfileDialog profile={removing} onClose={() => setRemoving(null)} />}
    </section>
  );
}

/** Old /profiles links land on the Cameras page section. */
export function Profiles() {
  return <Navigate to="/cameras#profiles" replace />;
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
  onRemove: () => void;
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
            {!editable && <Tag kind="pending">config.yaml</Tag>}
            {p.default && <Tag title={t("profiles.defaultHint")}>{t("profiles.default")}</Tag>}
            {!p.enabled && <Tag kind="warn">{t("profiles.disabled")}</Tag>}
          </span>
        }
      />
      <dl className="facts">
        <dt>{t("profiles.targets")}</dt>
        <dd>
          <span className="row wrap tags">
            {p.targets.map((x) => (
              <Tag key={x} kind="target">
                {x}
              </Tag>
            ))}
          </span>
        </dd>
        {aliases.length > 0 && (
          <>
            <dt>{t("profiles.aliases")}</dt>
            <dd>
              <span className="chips">
                {aliases.map(([from, to]) => (
                  <span key={from}>
                    {from} → {to}
                  </span>
                ))}
              </span>
            </dd>
          </>
        )}
        <dt>{t("profiles.minConfidence")}</dt>
        <dd>{percent(p.min_confidence)}</dd>
        <dt>{t("profiles.requires")}</dt>
        <dd>
          {p.requires.length ? (
            <span className="row wrap tags">
              {p.requires.map((x) => (
                <Tag key={x}>{x}</Tag>
              ))}
            </span>
          ) : (
            <span className="muted">{t("profiles.anyWorker")}</span>
          )}
        </dd>
        <dt>{t("profiles.fps")}</dt>
        <dd>{p.fps ? `${p.fps} fps` : <span className="muted">{t("profiles.allFrames")}</span>}</dd>
        {p.events_days !== null && (
          <>
            <dt>{t("profiles.keepEvents")}</dt>
            <dd>
              {p.events_days ? t("profiles.days", { n: p.events_days }) : t("profiles.forever")}
            </dd>
          </>
        )}
        <dt>{t("nav.cameras")}</dt>
        <dd>
          {p.cameras.length ? (
            p.cameras.map(cameraName).join(", ")
          ) : (
            <span className="muted">{t("profiles.noCameras")}</span>
          )}
        </dd>
        <dt>{t("profiles.workers")}</dt>
        <dd>
          {p.workers_online.length ? (
            <span className="chips">
              {p.workers_online.map((w) => (
                <span key={w}>{w}</span>
              ))}
            </span>
          ) : (
            <span className="muted">{t("profiles.noWorkers")}</span>
          )}
        </dd>
        <dt>{t("profiles.waiting")}</dt>
        <dd>{p.waiting_jobs}</dd>
      </dl>
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
          <Button size="small" icon={<Delete20Regular />} onClick={onRemove}>
            {t("common.remove")}
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}

function ProfileDialog({ profile, onClose }: { profile: Profile | null; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const tags = useQuery({ queryKey: ["worker-tags"], queryFn: api.workerTags });
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
  const [requires, setRequires] = useState(profile?.requires.join(", ") ?? "");
  const [fps, setFps] = useState(profile?.fps?.toString() ?? "");
  const [eventsDays, setEventsDays] = useState(profile?.events_days?.toString() ?? "");
  const [isDefault, setDefault] = useState(profile?.default ?? false);
  const [enabled, setEnabled] = useState(profile?.enabled ?? true);

  const body = (): ProfileInput => ({
    id: id.trim(),
    name: name.trim() || null,
    targets: targets.map((s) => s.trim()).filter(Boolean),
    aliases: aliasMap(aliases),
    min_confidence: Math.min(Math.max(Number(minConf) / 100, 0), 1),
    requires: splitList(requires).map((s) => s.toLowerCase()),
    fps: fps ? Number(fps) : null,
    default: isDefault,
    events_days: eventsDays === "" ? null : Number(eventsDays),
    enabled,
  });
  const save = useMutation({
    mutationFn: () => (profile ? api.updateProfile(body()) : api.createProfile(body())),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["profiles"] });
      void client.invalidateQueries({ queryKey: ["cameras"] });
      onClose();
    },
  });
  const known = tags.data ?? [];
  const warnings = profileWarnings(
    targets,
    aliasMap(aliases),
    splitList(requires).map((s) => s.toLowerCase()),
    labels.data,
  );
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
              <Field label={t("profiles.minConfidencePercent")}>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={minConf}
                  onChange={(_, d) => setMinConf(d.value)}
                />
              </Field>
              <Field
                label={t("profiles.requires")}
                hint={
                  known.length
                    ? t("profiles.requiresHintKnown", { tags: known.join(", ") })
                    : t("profiles.requiresHint")
                }
              >
                <Input
                  value={requires}
                  placeholder="speciesnet"
                  onChange={(_, d) => setRequires(d.value)}
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
                disabled={!id.trim() || !targets.length || save.isPending}
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

function RemoveProfileDialog({ profile, onClose }: { profile: Profile; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteProfile(profile.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["profiles"] });
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["queue"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("profiles.removeTitle", { name: profile.display_name })}</DialogTitle>
          <DialogContent>
            <Body1>{t("profiles.removeBody")}</Body1>
            {remove.error && (
              <MessageBar intent="error">
                <MessageBarBody>{remove.error.message}</MessageBarBody>
              </MessageBar>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button
              appearance="primary"
              icon={<Delete20Regular />}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              {t("common.remove")}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
