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
  Switch,
  Text,
  Textarea,
} from "@fluentui/react-components";
import { Add20Regular, Delete20Regular, Edit20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Profile, type ProfileInput } from "../api";
import { useProfiles } from "../components/ProfileSelect";
import { Empty, PageHeader, QueryState, Tag } from "../components/ui";
import { percent } from "../format";
import { useI18n } from "../i18n";

const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** "red fox = fox" lines <-> {"red fox": "fox"} */
function parseAliases(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const [from, to] = line.split("=").map((s) => s?.trim());
    if (from && to) out[from] = to;
  }
  return out;
}
const formatAliases = (aliases: Record<string, string>) =>
  Object.entries(aliases)
    .map(([from, to]) => `${from} = ${to}`)
    .join("\n");

export function Profiles() {
  const { t, rich } = useI18n();
  const profiles = useProfiles();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [removing, setRemoving] = useState<Profile | null>(null);
  const cameraName = (id: string) => cameras.data?.find((c) => c.id === id)?.name ?? id;

  return (
    <>
      <PageHeader
        title={t("profiles.title")}
        actions={
          <Button appearance="primary" icon={<Add20Regular />} onClick={() => setEditing("new")}>
            {t("profiles.add")}
          </Button>
        }
      >
        {rich("profiles.intro", { file: <code>config.yaml</code> })}
      </PageHeader>
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
    </>
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
          <span className="row wrap">
            {!editable && <Tag kind="pending">config.yaml</Tag>}
            {p.default && <Tag title={t("profiles.defaultHint")}>{t("profiles.default")}</Tag>}
            {!p.enabled && <Tag kind="warn">{t("profiles.disabled")}</Tag>}
          </span>
        }
      />
      <dl className="facts">
        <dt>{t("profiles.targets")}</dt>
        <dd>
          <span className="row wrap">
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
            <span className="row wrap">
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
  const [targets, setTargets] = useState(profile?.targets.join(", ") ?? "");
  const [aliases, setAliases] = useState(formatAliases(profile?.aliases ?? {}));
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
    targets: splitList(targets),
    aliases: parseAliases(aliases),
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

  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface className="add-camera-dialog">
        <form
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
                <Input value={targets} onChange={(_, d) => setTargets(d.value)} />
              </Field>
              <Field label={t("profiles.aliases")} hint={t("profiles.aliasesHint")}>
                <Textarea
                  value={aliases}
                  resize="vertical"
                  placeholder="red fox = fox"
                  onChange={(_, d) => setAliases(d.value)}
                />
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
                disabled={!id.trim() || !splitList(targets).length || save.isPending}
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
