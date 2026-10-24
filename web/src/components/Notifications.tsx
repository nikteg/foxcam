import {
  Subtitle2,
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  Checkbox,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Textarea,
} from "@fluentui/react-components";
import {
  Add20Regular,
  Alert20Regular,
  Delete20Regular,
  Edit20Regular,
  Send20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  api,
  type Channel,
  type ChannelInput,
  type ChannelType,
  type NotifyTrigger,
  type ProbeResult,
} from "../api";
import { percent } from "../format";
import { useI18n, type MessageKey } from "../i18n";
import { LabelPicker, useLabelSuggestions, useSuggestionList } from "./LabelPicker";
import { useCameraName } from "./CameraSelect";
import { useProfileName, useProfiles } from "./ProfileSelect";
import { Empty, QueryState, Tag, Time } from "./ui";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";

const TYPE_LABELS: Record<ChannelType, MessageKey> = {
  ntfy: "notify.type.ntfy",
  webhook: "notify.type.webhook",
  apprise: "notify.type.apprise",
};
const TRIGGERS: NotifyTrigger[] = ["event.new", "event.clip"];
const TRIGGER_LABELS: Record<NotifyTrigger, MessageKey> = {
  "event.new": "notify.on.new",
  "event.clip": "notify.on.clip",
};

const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** Add or remove `value` from a filter list; null means "all". */
function toggle<T>(list: T[] | null, all: T[], value: T, checked: boolean): T[] | null {
  const next = checked ? [...(list ?? []), value] : (list ?? all).filter((x) => x !== value);
  return next.length === 0 || next.length === all.length ? null : next;
}

/** Notification channels (ntfy, webhooks, Apprise) with a test button and recent deliveries. */
export function Notifications() {
  const { t, rich } = useI18n();
  const overview = useQuery({ queryKey: ["notifications"], queryFn: api.notifications });
  const deliveries = useQuery({
    queryKey: ["notifications", "deliveries"],
    queryFn: api.deliveries,
    refetchInterval: 15_000,
  });
  const [editing, setEditing] = useState<Channel | "new" | null>(null);
  const [removing, setRemoving] = useState<Channel | null>(null);
  const data = overview.data;

  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("settings.notifications")}
        <Button
          size="small"
          appearance="primary"
          icon={<Add20Regular />}
          className="section-action"
          onClick={() => setEditing("new")}
        >
          {t("notify.add")}
        </Button>
      </Subtitle2>
      <p className="muted">{t("notify.intro")}</p>
      <QueryState error={overview.error} loading={overview.isPending} />
      {data && !data.public_url && (
        <MessageBar intent="info" className="block">
          <MessageBarBody>
            {rich("notify.noPublicUrl", {
              key: <code>public_url</code>,
              file: <code>config.yaml</code>,
            })}
          </MessageBarBody>
        </MessageBar>
      )}
      {data?.channels.length === 0 && <Empty>{t("notify.none")}</Empty>}
      <div className="cards">
        {data?.channels.map((ch) => (
          <ChannelCard
            key={ch.id}
            channel={ch}
            onEdit={() => setEditing(ch)}
            onRemove={() => setRemoving(ch)}
          />
        ))}
      </div>
      {deliveries.data && deliveries.data.length > 0 && (
        <Deliveries
          channelName={(id) => data?.channels.find((c) => c.id === id)?.display_name ?? id}
        />
      )}
      {editing && data && (
        <ChannelDialog
          channel={editing === "new" ? null : editing}
          appriseInstalled={data.apprise}
          onClose={() => setEditing(null)}
        />
      )}
      {removing && <RemoveDialog channel={removing} onClose={() => setRemoving(null)} />}
    </>
  );
}

function ChannelCard({
  channel: c,
  onEdit,
  onRemove,
}: {
  channel: Channel;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const cameraName = useCameraName();
  const profileName = useProfileName();
  const [result, setResult] = useState<ProbeResult | null>(null);
  const test = useMutation({ mutationFn: () => api.testChannel(c.id), onSuccess: setResult });
  const target = c.type === "ntfy" ? `${c.server.replace(/\/$/, "")}/${c.topic}` : (c.url ?? "");

  return (
    <Card>
      <CardHeader
        image={<Alert20Regular />}
        header={
          <span className="row">
            <Text weight="semibold">{c.display_name}</Text>
            <Caption1 className="muted">{c.id}</Caption1>
          </span>
        }
        description={
          <span className="row wrap tags">
            <Tag>{t(TYPE_LABELS[c.type])}</Tag>
            {c.managed === "config" && <Tag kind="pending">config.yaml</Tag>}
            {!c.enabled && <Tag kind="warn">{t("notify.disabled")}</Tag>}
          </span>
        }
      />
      <dl className="facts">
        <dt>{t("notify.target")}</dt>
        <dd className="mono">{target}</dd>
        <dt>{t("notify.when")}</dt>
        <dd>{c.when.map((o) => t(TRIGGER_LABELS[o])).join(", ")}</dd>
        <dt>{t("nav.cameras")}</dt>
        <dd>{c.cameras ? c.cameras.map(cameraName).join(", ") : t("notify.all")}</dd>
        {c.profiles && (
          <>
            <dt>{t("profiles.title")}</dt>
            <dd>{c.profiles.map((p) => profileName(p)).join(", ")}</dd>
          </>
        )}
        {(c.labels || c.min_confidence > 0 || c.min_agreement > 1) && (
          <>
            <dt>{t("notify.only")}</dt>
            <dd>
              {[
                c.labels?.join(", "),
                c.min_confidence > 0 && `≥ ${percent(c.min_confidence)}`,
                c.min_agreement > 1 && t("events.seenByAtLeast", { n: c.min_agreement }),
              ]
                .filter(Boolean)
                .join(" – ")}
            </dd>
          </>
        )}
        {c.quiet_hours && (
          <>
            <dt>{t("notify.quietHours")}</dt>
            <dd>{c.quiet_hours.replace("-", " – ")}</dd>
          </>
        )}
        {c.cooldown_minutes > 0 && (
          <>
            <dt>{t("notify.cooldown")}</dt>
            <dd>{t("notify.minutes", { n: c.cooldown_minutes })}</dd>
          </>
        )}
        <dt>{t("notify.image")}</dt>
        <dd>{c.include_image && c.type !== "webhook" ? t("common.yes") : t("common.no")}</dd>
      </dl>
      {result && (
        <MessageBar intent={result.ok ? "success" : "error"}>
          <MessageBarBody>{result.ok ? t("notify.testOk") : result.detail}</MessageBarBody>
        </MessageBar>
      )}
      <CardFooter className="wrap">
        <Button
          size="small"
          icon={<Send20Regular />}
          disabled={test.isPending}
          onClick={() => test.mutate()}
        >
          {test.isPending ? t("notify.testing") : t("notify.test")}
        </Button>
        {c.managed === "ui" && (
          <>
            <Button size="small" icon={<Edit20Regular />} onClick={onEdit}>
              {t("common.edit")}
            </Button>
            <Button size="small" icon={<Delete20Regular />} onClick={onRemove}>
              {t("common.remove")}
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  );
}

function Deliveries({ channelName }: { channelName: (id: string) => string }) {
  const { t } = useI18n();
  const cameraName = useCameraName();
  const { data = [] } = useQuery({
    queryKey: ["notifications", "deliveries"],
    queryFn: api.deliveries,
  });
  const statusKind = { sent: "done", failed: "failed", pending: "pending", skipped: "warn" };
  return (
    <>
      <Text weight="semibold" className="section-sub">
        {t("notify.recent")}
      </Text>
      <Table className="table responsive" aria-label={t("notify.recent")}>
        <TableHeader>
          <TableRow>
            <TableHeaderCell>{t("notify.col.time")}</TableHeaderCell>
            <TableHeaderCell>{t("notify.col.channel")}</TableHeaderCell>
            <TableHeaderCell>{t("notify.col.event")}</TableHeaderCell>
            <TableHeaderCell>{t("col.state")}</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((d) => (
            <TableRow key={d.id}>
              <TableCell data-label={t("notify.col.time")}>
                <Time iso={d.sent_at ?? d.created_at} relative />
              </TableCell>
              <TableCell data-label={t("notify.col.channel")}>
                {channelName(d.channel_id)}
              </TableCell>
              <TableCell data-label={t("notify.col.event")}>
                {d.label ?? "–"} – {cameraName(d.camera_id)} –{" "}
                <span className="muted">{t(TRIGGER_LABELS[d.kind])}</span>
              </TableCell>
              <TableCell data-label={t("col.state")}>
                <Tag kind={statusKind[d.status]} title={d.error ?? undefined}>
                  {t(`notify.status.${d.status}` as MessageKey)}
                </Tag>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

function ChannelDialog({
  channel,
  appriseInstalled,
  onClose,
}: {
  channel: Channel | null;
  appriseInstalled: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const profiles = useProfiles();
  const [type, setType] = useState<ChannelType>(channel?.type ?? "ntfy");
  const [id, setId] = useState(channel?.id ?? "");
  const [name, setName] = useState(channel?.name ?? "");
  const [when, setWhen] = useState<NotifyTrigger[]>(channel?.when ?? ["event.new"]);
  const [cams, setCams] = useState<string[] | null>(channel?.cameras ?? null);
  const [profs, setProfs] = useState<string[] | null>(channel?.profiles ?? null);
  const [labels, setLabels] = useState<string[]>(channel?.labels ?? []);
  const labelData = useLabelSuggestions();
  const profileTargets = useMemo(
    () => ({
      labels: [...new Set((profiles.data ?? []).flatMap((p) => p.targets))],
      source: t("labels.profileTarget"),
    }),
    [profiles.data, t],
  );
  const labelSuggestions = useSuggestionList(labelData.data, profileTargets);
  const [minConf, setMinConf] = useState(String(Math.round((channel?.min_confidence ?? 0) * 100)));
  const [minAgreement, setMinAgreement] = useState(String(channel?.min_agreement ?? 1));
  const [quietFrom, setQuietFrom] = useState(channel?.quiet_hours?.split("-")[0] ?? "");
  const [quietTo, setQuietTo] = useState(channel?.quiet_hours?.split("-")[1] ?? "");
  const [cooldown, setCooldown] = useState(String(channel?.cooldown_minutes ?? 0));
  const [image, setImage] = useState(channel?.include_image ?? true);
  const [enabled, setEnabled] = useState(channel?.enabled ?? true);
  const [server, setServer] = useState(channel?.server ?? "https://ntfy.sh");
  const [topic, setTopic] = useState(channel?.topic ?? "");
  const [token, setToken] = useState("");
  const [priority, setPriority] = useState(String(channel?.priority ?? 3));
  const [tags, setTags] = useState(channel?.tags.join(", ") ?? "");
  const [url, setUrl] = useState(channel?.type === "apprise" ? "" : (channel?.url ?? ""));
  const [secret, setSecret] = useState("");
  const [headers, setHeaders] = useState(
    Object.entries(channel?.headers ?? {})
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n"),
  );

  const body = (): ChannelInput => ({
    id: id.trim(),
    name: name.trim() || null,
    type,
    enabled,
    when,
    cameras: cams,
    profiles: profs,
    labels: labels.length ? labels : null,
    min_confidence: Math.min(Math.max(Number(minConf) / 100, 0), 1),
    min_agreement: Math.min(Math.max(Math.round(Number(minAgreement)) || 1, 1), 10),
    quiet_hours: quietFrom && quietTo ? `${quietFrom}-${quietTo}` : null,
    cooldown_minutes: Math.max(Number(cooldown) || 0, 0),
    include_image: image,
    server: server.trim() || "https://ntfy.sh",
    topic: topic.trim() || null,
    token: token || null,
    username: null,
    password: null,
    priority: Number(priority),
    tags: splitList(tags),
    url: url.trim() || null,
    secret: secret || null,
    headers: Object.fromEntries(
      headers
        .split("\n")
        .map((line) => line.split(":").map((s) => s.trim()))
        .filter(([k, ...v]) => k && v.join(":"))
        .map(([k, ...v]) => [k, v.join(":")]),
    ),
  });
  const save = useMutation({
    mutationFn: () => (channel ? api.updateChannel(body()) : api.createChannel(body())),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["notifications"] });
      onClose();
    },
  });
  const camIds = (cameras.data ?? []).map((c) => c.id);
  const profIds = (profiles.data ?? []).map((p) => p.id);
  const keepHint = channel ? t("notify.keepSecret") : undefined;

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
              {channel ? t("notify.editTitle", { name: channel.display_name }) : t("notify.add")}
            </DialogTitle>
            <DialogContent className="form-stack">
              <Field label={t("notify.type")}>
                <Select
                  value={type}
                  disabled={!!channel}
                  onChange={(_, d) => setType(d.value as ChannelType)}
                >
                  {(Object.keys(TYPE_LABELS) as ChannelType[]).map((k) => (
                    <option key={k} value={k}>
                      {t(TYPE_LABELS[k])}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("profiles.name")}>
                <Input
                  value={name}
                  placeholder={t("notify.namePlaceholder")}
                  onChange={(_, d) => setName(d.value)}
                />
              </Field>
              {!channel && (
                <Field label={t("profiles.id")} hint={t("profiles.idHint")} required>
                  <Input
                    value={id}
                    onChange={(_, d) => setId(d.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))}
                  />
                </Field>
              )}

              {type === "ntfy" && (
                <>
                  <Field label={t("notify.server")} hint={t("notify.serverHint")}>
                    <Input value={server} onChange={(_, d) => setServer(d.value)} />
                  </Field>
                  <Field label={t("notify.topic")} hint={t("notify.topicHint")} required>
                    <Input value={topic} onChange={(_, d) => setTopic(d.value)} />
                  </Field>
                  <Field label={t("notify.token")} hint={keepHint ?? t("notify.tokenHint")}>
                    <Input
                      type="password"
                      value={token}
                      placeholder={channel?.has_token ? "••••••" : ""}
                      onChange={(_, d) => setToken(d.value)}
                    />
                  </Field>
                  <Field label={t("notify.priority")}>
                    <Select value={priority} onChange={(_, d) => setPriority(d.value)}>
                      {[1, 2, 3, 4, 5].map((p) => (
                        <option key={p} value={p}>
                          {t(`notify.priority.${p}` as MessageKey)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={t("notify.tags")} hint={t("notify.tagsHint")}>
                    <Input value={tags} onChange={(_, d) => setTags(d.value)} />
                  </Field>
                </>
              )}
              {type === "webhook" && (
                <>
                  <Field label={t("notify.url")} hint={t("notify.webhookHint")} required>
                    <Input
                      value={url}
                      placeholder="https://"
                      onChange={(_, d) => setUrl(d.value)}
                    />
                  </Field>
                  <Field label={t("notify.secret")} hint={keepHint ?? t("notify.secretHint")}>
                    <Input
                      type="password"
                      value={secret}
                      placeholder={channel?.has_secret ? "••••••" : ""}
                      onChange={(_, d) => setSecret(d.value)}
                    />
                  </Field>
                  <Field label={t("notify.headers")} hint={t("notify.headersHint")}>
                    <Textarea
                      value={headers}
                      resize="vertical"
                      placeholder="Authorization: Bearer …"
                      onChange={(_, d) => setHeaders(d.value)}
                    />
                  </Field>
                </>
              )}
              {type === "apprise" && (
                <>
                  {!appriseInstalled && (
                    <MessageBar intent="warning">
                      <MessageBarBody>{t("notify.appriseMissing")}</MessageBarBody>
                    </MessageBar>
                  )}
                  <Field
                    label={t("notify.appriseUrl")}
                    hint={channel ? t("notify.keepSecret") : t("notify.appriseHint")}
                    required={!channel}
                  >
                    <Input
                      type="password"
                      value={url}
                      placeholder={channel ? "••••••" : "tgram://bottoken/chatid"}
                      onChange={(_, d) => setUrl(d.value)}
                    />
                  </Field>
                </>
              )}

              <Field label={t("notify.when")}>
                <div className="row wrap">
                  {TRIGGERS.map((tr) => (
                    <Checkbox
                      key={tr}
                      label={t(TRIGGER_LABELS[tr])}
                      checked={when.includes(tr)}
                      onChange={(_, d) =>
                        setWhen((cur) => (d.checked ? [...cur, tr] : cur.filter((x) => x !== tr)))
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
                      checked={cams === null || cams.includes(c.id)}
                      onChange={(_, d) => setCams(toggle(cams, camIds, c.id, d.checked === true))}
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
                        checked={profs === null || profs.includes(p.id)}
                        onChange={(_, d) =>
                          setProfs(toggle(profs, profIds, p.id, d.checked === true))
                        }
                      />
                    ))}
                  </div>
                </Field>
              )}
              <Field label={t("notify.labels")} hint={t("notify.labelsHint")}>
                <LabelPicker
                  value={labels}
                  onChange={setLabels}
                  suggestions={labelSuggestions}
                  placeholder="fox"
                  ariaLabel={t("notify.labels")}
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
              <Field label={t("notify.minAgreement")} hint={t("notify.minAgreementHint")}>
                <Input
                  type="number"
                  min={1}
                  max={10}
                  value={minAgreement}
                  onChange={(_, d) => setMinAgreement(d.value)}
                />
              </Field>
              <Field label={t("notify.quietHours")} hint={t("notify.quietHint")}>
                <div className="row wrap">
                  <Input type="time" value={quietFrom} onChange={(_, d) => setQuietFrom(d.value)} />
                  <span>–</span>
                  <Input type="time" value={quietTo} onChange={(_, d) => setQuietTo(d.value)} />
                </div>
              </Field>
              <Field label={t("notify.cooldownMinutes")} hint={t("notify.cooldownHint")}>
                <Input
                  type="number"
                  min={0}
                  value={cooldown}
                  onChange={(_, d) => setCooldown(d.value)}
                />
              </Field>
              {type !== "webhook" && (
                <Switch
                  label={t("notify.includeImage")}
                  checked={image}
                  onChange={(_, d) => setImage(d.checked)}
                />
              )}
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
                disabled={!id.trim() || when.length === 0 || save.isPending}
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

function RemoveDialog({ channel, onClose }: { channel: Channel; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteChannel(channel.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["notifications"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("notify.removeTitle", { name: channel.display_name })}</DialogTitle>
          <DialogContent>{t("notify.removeBody")}</DialogContent>
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
