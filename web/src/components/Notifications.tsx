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
  Select,
  Switch,
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
import { useState } from "react";

import { api, type Channel, type ChannelInput, type ChannelType, type Delivery } from "../api";
import { percent } from "../format";
import { ConfirmPopover } from "./ConfirmPopover";
import { DataTable, type Column } from "./DataTable";
import { useI18n, type MessageKey } from "../i18n";
import { CameraTag } from "./CameraSelect";
import { ProfileTag } from "./ProfileSelect";
import {
  ConfigTag,
  Empty,
  Fact,
  Facts,
  InfoBanner,
  PasswordInput,
  QueryState,
  RouterLink,
  Tag,
  Time,
  Toolbar,
} from "./ui";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";
import { CardsSkeleton } from "./Skeletons";
import { useToast } from "./Toasts";
import {
  draftFromRules,
  NotificationRulesFields,
  rulesFromDraft,
  TRIGGER_LABELS,
} from "./NotificationRulesFields";

const TYPE_LABELS: Record<ChannelType, MessageKey> = {
  ntfy: "notify.type.ntfy",
  webhook: "notify.type.webhook",
  apprise: "notify.type.apprise",
};
/** Types that can send the detection frame (webhooks get a link). */
const IMAGE_TYPES = new Set<ChannelType>(["ntfy", "apprise"]);
const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** Notification channels (ntfy, webhooks, Apprise) with a test button and recent deliveries.
 * Everyone's own push notifications are on their Account page. */
export function Notifications() {
  const client = useQueryClient();
  const { t, rich } = useI18n();
  const overview = useQuery({ queryKey: ["notifications"], queryFn: api.notifications });
  const deliveries = useQuery({
    queryKey: ["notifications", "deliveries"],
    queryFn: api.deliveries,
    refetchInterval: 15_000,
  });
  const [editing, setEditing] = useState<Channel | "new" | null>(null);
  const data = overview.data;

  return (
    <>
      <Toolbar>
        <Button appearance="primary" icon={<Add20Regular />} onClick={() => setEditing("new")}>
          {t("notify.add")}
        </Button>
      </Toolbar>
      <InfoBanner id="notifications">{t("notify.intro")}</InfoBanner>
      <QueryState
        error={overview.error}
        loading={overview.isPending}
        skeleton={<CardsSkeleton count={2} thumb={false} />}
      />
      {data && !data.public_url && (
        <MessageBar intent="info" className="block">
          <MessageBarBody>
            {rich("notify.noPublicUrl", {
              link: <RouterLink to="/settings">{t("notify.publicUrlLink")}</RouterLink>,
            })}
          </MessageBarBody>
        </MessageBar>
      )}
      {data?.channels.length === 0 && <Empty>{t("notify.none")}</Empty>}
      <div className="cards block">
        {data?.channels.map((ch) => (
          <ChannelCard
            key={ch.id}
            channel={ch}
            onEdit={() => setEditing(ch)}
            onRemove={() =>
              api
                .deleteChannel(ch.id)
                .then(() => client.invalidateQueries({ queryKey: ["notifications"] }))
            }
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
  onRemove: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const test = useMutation({
    mutationFn: () => api.testChannel(c.id),
    onSuccess: (r) => (r.ok ? toast.success(t("notify.testOk")) : toast.error(r.detail)),
    onError: (e) => toast.error(e),
  });
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
            {c.managed === "config" && <ConfigTag />}
            {!c.enabled && <Tag kind="warn">{t("notify.disabled")}</Tag>}
          </span>
        }
      />
      <Facts>
        <Fact label={t("notify.target")} className="mono">
          {target}
        </Fact>
        <Fact label={t("notify.when")}>
          <span className="chips">
            {c.when.map((o) => (
              <Tag key={o}>{t(TRIGGER_LABELS[o])}</Tag>
            ))}
          </span>
        </Fact>
        <Fact label={t("nav.cameras")}>
          {c.cameras ? (
            <span className="chips">
              {c.cameras.map((id) => (
                <CameraTag key={id} id={id} />
              ))}
            </span>
          ) : (
            t("notify.all")
          )}
        </Fact>
        {c.profiles && (
          <>
            <Fact label={t("profiles.title")}>
              <span className="chips">
                {c.profiles.map((p) => (
                  <ProfileTag key={p} id={p} />
                ))}
              </span>
            </Fact>
          </>
        )}
        {(c.labels || c.min_confidence > 0 || c.min_agreement > 1) && (
          <>
            <Fact label={t("notify.only")}>
              <span className="chips">
                {c.labels?.map((l) => (
                  <Tag key={l}>{l}</Tag>
                ))}
                {(c.min_confidence > 0 || c.min_agreement > 1) && (
                  <span>
                    {[
                      c.min_confidence > 0 && `≥ ${percent(c.min_confidence)}`,
                      c.min_agreement > 1 && t("events.seenByAtLeast", { n: c.min_agreement }),
                    ]
                      .filter(Boolean)
                      .join(" – ")}
                  </span>
                )}
              </span>
            </Fact>
          </>
        )}
        {c.quiet_hours && (
          <>
            <Fact label={t("notify.quietHours")}>{c.quiet_hours.replace("-", " – ")}</Fact>
          </>
        )}
        {c.cooldown_minutes > 0 && (
          <>
            <Fact label={t("notify.cooldown")}>
              {t("notify.minutes", { n: c.cooldown_minutes })}
            </Fact>
          </>
        )}
        <Fact label={t("notify.image")}>
          {c.include_image && IMAGE_TYPES.has(c.type) ? t("common.yes") : t("common.no")}
        </Fact>
      </Facts>
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
            <ConfirmPopover
              title={t("notify.removeTitle", { name: c.display_name })}
              body={t("notify.removeBody")}
              onConfirm={onRemove}
            >
              <Button size="small" icon={<Delete20Regular />}>
                {t("common.remove")}
              </Button>
            </ConfirmPopover>
          </>
        )}
      </CardFooter>
    </Card>
  );
}

function Deliveries({ channelName }: { channelName: (id: string) => string }) {
  const { t } = useI18n();
  const { data = [] } = useQuery({
    queryKey: ["notifications", "deliveries"],
    queryFn: api.deliveries,
  });
  const statusKind = { sent: "done", failed: "failed", pending: "pending", skipped: "warn" };
  const columns: Column<Delivery>[] = [
    {
      id: "time",
      header: t("notify.col.time"),
      width: 130,
      cell: (d) => <Time iso={d.sent_at ?? d.created_at} relative />,
    },
    {
      id: "channel",
      header: t("notify.col.channel"),
      width: 150,
      cell: (d) =>
        d.user !== null ? t("notify.personalOf", { name: d.user }) : channelName(d.channel_id),
    },
    { id: "event", header: t("notify.col.event"), width: 110, cell: (d) => d.label ?? "–" },
    {
      id: "camera",
      header: t("common.camera"),
      width: 150,
      cell: (d) => d.camera_id && <CameraTag id={d.camera_id} />,
    },
    {
      id: "trigger",
      header: t("notify.col.when"),
      width: 140,
      cell: (d) => t(TRIGGER_LABELS[d.kind]),
    },
    {
      id: "state",
      header: t("col.state"),
      width: 110,
      cell: (d) => (
        <Tag kind={statusKind[d.status]} title={d.error ?? undefined}>
          {t(`notify.status.${d.status}` as MessageKey)}
        </Tag>
      ),
    },
  ];
  return (
    <>
      <Text weight="semibold" className="section-sub">
        {t("notify.recent")}
      </Text>
      <DataTable
        ariaLabel={t("notify.recent")}
        items={data}
        getRowId={(d) => d.id}
        columns={columns}
      />
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
  const [type, setType] = useState<ChannelType>(channel?.type ?? "ntfy");
  const [id, setId] = useState(channel?.id ?? "");
  const [name, setName] = useState(channel?.name ?? "");
  const [rules, setRules] = useState(() => draftFromRules(channel));
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
    ...rulesFromDraft(rules),
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
                    <PasswordInput
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
                    <PasswordInput
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
                    <PasswordInput
                      value={url}
                      placeholder={channel ? "••••••" : "tgram://bottoken/chatid"}
                      onChange={(_, d) => setUrl(d.value)}
                    />
                  </Field>
                </>
              )}

              <NotificationRulesFields value={rules} onChange={setRules} />
              {IMAGE_TYPES.has(type) && (
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
                disabled={!id.trim() || rules.when.length === 0 || save.isPending}
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
