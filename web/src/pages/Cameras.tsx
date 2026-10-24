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
  Checkbox,
  Field,
  Input,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  MessageBarTitle,
  Radio,
  RadioGroup,
  Switch,
  Tab,
  TabList,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import {
  Add20Regular,
  Delete20Regular,
  EyeOff20Regular,
  Settings20Regular,
  Video20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { api, type Camera, type CameraSource, type RecordingProfile } from "../api";
import { useIsAdmin, hasRole, useMe } from "../auth";
import { AddCameraDialog } from "../components/AddCameraDialog";
import { SharePanel, useRoleName } from "../components/ShareDialog";
import { ProfileDialog, ProfilesSection } from "./Profiles";
import { useProfileName, useProfiles } from "../components/ProfileSelect";
import { TapoAccountCard } from "../components/TapoAccount";
import {
  Dot,
  Empty,
  PageHeader,
  QueryState,
  Tag,
  useRouterHref,
  useSourceLabel,
  PageTabs,
  useSubPage,
} from "../components/ui";
import { useI18n } from "../i18n";

function sourceDetail(source: CameraSource, synthetic: string): string {
  switch (source.type) {
    case "rtsp":
      return source.url;
    case "tapo_cloud":
      return `${source.device_name ?? source.device_id ?? "?"} – ${source.resolution ?? "HD"}`;
    case "file":
      return source.path.split("/").pop() ?? source.path;
    case "test":
      return synthetic;
  }
}

export function Cameras() {
  const { t, rich } = useI18n();
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: api.cameras,
    refetchInterval: 10_000,
  });
  const [adding, setAdding] = useState(false);
  const [addingProfile, setAddingProfile] = useState(false);
  const [settingsFor, setSettingsFor] = useState<Camera | null>(null);
  const me = useMe();
  const isAdmin = me?.is_admin ?? false;
  // Sub-pages with their own URL (links work even while parts are still loading).
  const navigate = useNavigate();
  const subPage = useSubPage("/cameras", ["cameras", "profiles", "integrations"] as const);
  const tab = isAdmin ? subPage : "cameras";

  return (
    <>
      <PageHeader
        title={t("nav.cameras")}
        actions={
          (isAdmin || me?.can_add_cameras) && (
            <div className="row wrap">
              {isAdmin && (
                <Button icon={<Add20Regular />} onClick={() => setAddingProfile(true)}>
                  {t("profiles.add")}
                </Button>
              )}
              {me?.can_add_cameras && (
                <Button
                  appearance="primary"
                  icon={<Add20Regular />}
                  onClick={() => setAdding(true)}
                >
                  {t("addCamera.title")}
                </Button>
              )}
            </div>
          )
        }
      >
        {isAdmin
          ? rich("cameras.intro", { file: <code>config.yaml</code> })
          : t("cameras.introUser")}
      </PageHeader>

      {isAdmin && (
        <PageTabs
          base="/cameras"
          current={tab}
          tabs={[
            { id: "cameras", label: t("nav.cameras") },
            { id: "profiles", label: t("profiles.title") },
            { id: "integrations", label: t("cameras.integrations") },
          ]}
        />
      )}

      {tab === "integrations" && (
        <div className="cards">
          <TapoAccountCard />
        </div>
      )}

      {tab === "profiles" && <ProfilesSection />}

      {tab === "cameras" && (
        <>
          <QueryState error={cameras.error} loading={cameras.isPending} />
          {cameras.data?.length === 0 && <Empty>{t("cameras.none")}</Empty>}
          <div className="cards">
            {cameras.data?.map((c) => (
              <CameraCard key={c.id} camera={c} onSettings={() => setSettingsFor(c)} />
            ))}
          </div>
        </>
      )}

      <AddCameraDialog open={adding} onClose={() => setAdding(false)} />
      {addingProfile && (
        <ProfileDialog
          profile={null}
          onSaved={() => void navigate("/cameras/profiles")}
          onClose={() => setAddingProfile(false)}
        />
      )}
      {settingsFor && (
        <CameraSettingsDialog
          // Fresh state per camera; the camera object itself updates via the query.
          key={settingsFor.id}
          camera={cameras.data?.find((c) => c.id === settingsFor.id) ?? settingsFor}
          onClose={() => setSettingsFor(null)}
        />
      )}
    </>
  );
}

function CameraCard({ camera: c, onSettings }: { camera: Camera; onSettings: () => void }) {
  const { t } = useI18n();
  const me = useMe();
  const profileName = useProfileName();
  const recordingName = useRecordingProfileName();
  const sourceLabel = useSourceLabel();
  const client = useQueryClient();
  const routerHref = useRouterHref();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.updateCamera(c.id, { enabled }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cameras"] }),
  });
  const roleName = useRoleName();
  const canEdit = hasRole(c, "editor");
  const isOwner = hasRole(c, "owner");
  const editable = c.managed === "ui" && canEdit;

  return (
    <Card>
      <CardHeader
        header={
          <span className="row">
            <Dot ok={c.online} />
            <Text weight="semibold">{c.name}</Text>
            <Caption1 className="muted">{c.id}</Caption1>
          </span>
        }
        description={
          <span className="row wrap tags">
            <Tag>{sourceLabel(c.source_config.type)}</Tag>
            {c.managed === "config" && <Tag kind="pending">config.yaml</Tag>}
            {me?.enabled && c.role !== "owner" && <Tag>{roleName(c.role)}</Tag>}
            {!c.enabled && <Tag kind="warn">{t("cameras.disabled")}</Tag>}
            {c.private && <Tag kind="private">{t("state.private")}</Tag>}
          </span>
        }
      />
      <dl className="facts">
        <dt>{t("cameras.source")}</dt>
        <dd className="mono">{sourceDetail(c.source_config, t("cameras.synthetic"))}</dd>
        <dt>{t("camera.segmentLength")}</dt>
        <dd>
          {c.segment_seconds} s
          {c.segment_seconds_override === null && (
            <span className="muted"> ({t("camera.default")})</span>
          )}
        </dd>
        <dt>{t("recording.title")}</dt>
        <dd>
          <span className="row wrap tags">
            {recordingName(c.recording_profile)}
            {c.burn_masks && c.rects.length > 0 && <Tag>{t("recording.masksBurnedIn")}</Tag>}
          </span>
        </dd>
        <dt>{t("camera.censorAreas")}</dt>
        <dd>{c.rects.length}</dd>
        <dt>{t("profiles.title")}</dt>
        <dd>
          {c.profiles.length ? (
            <span className="row wrap tags">
              {c.profiles.map((id) => (
                <Tag key={id}>{profileName(id)}</Tag>
              ))}
            </span>
          ) : (
            <span className="muted">{t("cameras.noProfiles")}</span>
          )}
        </dd>
      </dl>
      <Tooltip
        content={
          editable
            ? t("cameras.disableHint")
            : c.managed === "config"
              ? t("cameras.configHint")
              : t("cameras.editorsOnly")
        }
        relationship="description"
      >
        <Switch
          label={c.enabled ? t("cameras.recording") : t("cameras.disabled")}
          checked={c.enabled}
          disabled={!editable || toggle.isPending}
          onChange={(_, d) => toggle.mutate(d.checked)}
        />
      </Tooltip>
      <CardFooter className="wrap">
        <Button
          as="a"
          size="small"
          icon={<Video20Regular />}
          {...routerHref(`/live?camera=${c.id}`)}
        >
          {t("nav.live")}
        </Button>
        {(canEdit || isOwner) && (
          <Button size="small" icon={<Settings20Regular />} onClick={onSettings}>
            {t("cameras.settings")}
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}

function GeneralPanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const routerHref = useRouterHref();
  const editable = camera.managed === "ui" && hasRole(camera, "editor");
  const canRemove = camera.managed === "ui" && hasRole(camera, "owner");
  const [name, setName] = useState(camera.name);
  const [segment, setSegment] = useState(camera.segment_seconds_override?.toString() ?? "");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const save = useMutation({
    mutationFn: () =>
      api.updateCamera(camera.id, {
        name,
        ...(segment ? { segment_seconds: Number(segment) } : { clear_segment_seconds: true }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteCamera(camera.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["status"] });
      onClose();
    },
  });
  const error = save.error ?? remove.error;

  return (
    <form
      className="contents"
      onSubmit={(e) => {
        e.preventDefault();
        if (editable) save.mutate();
      }}
    >
      <DialogContent className="form-stack">
        {tabs}
        {editable ? (
          <>
            <Field label={t("camera.name")} required>
              <Input value={name} onChange={(_, d) => setName(d.value)} />
            </Field>
            <Field label={t("camera.segmentLengthSeconds")} hint={t("camera.segmentLengthHint")}>
              <Input
                type="number"
                min={5}
                max={3600}
                value={segment}
                placeholder={t("camera.default")}
                onChange={(_, d) => setSegment(d.value)}
              />
            </Field>
            <Caption1 className="muted">{t("cameras.changeSourceHint")}</Caption1>
          </>
        ) : (
          <Body1 className="muted">{t("cameras.configHint")}</Body1>
        )}
        <div className="row wrap">
          <Button
            as="a"
            size="small"
            icon={<EyeOff20Regular />}
            {...routerHref(`/cameras/${camera.id}/mask`)}
          >
            {t("cameras.editCensorAreas")}
          </Button>
          {canRemove && !confirmRemove && (
            <Button size="small" icon={<Delete20Regular />} onClick={() => setConfirmRemove(true)}>
              {t("cameras.removeCamera")}
            </Button>
          )}
        </div>
        {confirmRemove && (
          <MessageBar intent="warning">
            <MessageBarBody>
              <MessageBarTitle>{t("cameras.removeTitle", { name: camera.name })}</MessageBarTitle>
              {t("cameras.removeBody")}
            </MessageBarBody>
            <MessageBarActions>
              <Button size="small" onClick={() => setConfirmRemove(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                size="small"
                appearance="primary"
                icon={<Delete20Regular />}
                disabled={remove.isPending}
                onClick={() => remove.mutate()}
              >
                {t("common.remove")}
              </Button>
            </MessageBarActions>
          </MessageBar>
        )}
        {error && (
          <MessageBar intent="error">
            <MessageBarBody>{error.message}</MessageBarBody>
          </MessageBar>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{editable ? t("common.cancel") : t("common.close")}</Button>
        {editable && (
          <Button type="submit" appearance="primary" disabled={!name || save.isPending}>
            {t("common.save")}
          </Button>
        )}
      </DialogActions>
    </form>
  );
}

type SettingsTab = "general" | "detection" | "recording" | "sharing";

/** Everything about one camera, under tabs (the card only keeps Live and Settings). */
function CameraSettingsDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { t } = useI18n();
  const me = useMe();
  const canEdit = hasRole(camera, "editor");
  const canShare = !!me?.enabled && hasRole(camera, "owner");
  const [tab, setTab] = useState<SettingsTab>("general");
  const tabs = (
    <TabList
      className="block"
      selectedValue={tab}
      onTabSelect={(_, d) => setTab(d.value as SettingsTab)}
    >
      <Tab value="general">{t("cameras.tabGeneral")}</Tab>
      {canEdit && <Tab value="detection">{t("cameras.detect")}</Tab>}
      {canEdit && <Tab value="recording">{t("recording.title")}</Tab>}
      {canShare && <Tab value="sharing">{t("share.button")}</Tab>}
    </TabList>
  );
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface className="camera-settings-dialog">
        <DialogBody>
          <DialogTitle>{t("cameras.settingsTitle", { name: camera.name })}</DialogTitle>
          {tab === "general" && <GeneralPanel camera={camera} tabs={tabs} onClose={onClose} />}
          {tab === "detection" && <DetectionPanel camera={camera} tabs={tabs} onClose={onClose} />}
          {tab === "recording" && <RecordingPanel camera={camera} tabs={tabs} onClose={onClose} />}
          {tab === "sharing" && <SharePanel camera={camera} tabs={tabs} onClose={onClose} />}
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function useRecordingProfiles() {
  return useQuery({ queryKey: ["recording-profiles"], queryFn: api.recordingProfiles });
}

function useRecordingProfileName(): (id: string) => string {
  const { data = [] } = useRecordingProfiles();
  return (id) => data.find((p) => p.id === id)?.display_name ?? id;
}

/** "≤ 720p, 1200 kbit/s, 15 fps, H.264, kept 30 days" */
function useRecordingSummary(): (p: RecordingProfile) => string {
  const { t } = useI18n();
  return (p) => {
    const parts = p.transcode
      ? [
          p.max_height ? `≤ ${p.max_height}p` : t("recording.sameResolution"),
          p.bitrate_kbit ? `${p.bitrate_kbit} kbit/s` : t("recording.constantQuality"),
          ...(p.fps ? [`${p.fps} fps`] : []),
          p.codec === "h265" ? "H.265" : "H.264",
        ]
      : [t("recording.asSent")];
    if (p.keep_days !== null) parts.push(t("recording.keptDays", { n: p.keep_days }));
    return parts.join(", ");
  };
}

function RecordingPanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const list = useRecordingProfiles();
  const profiles = list.data ?? [];
  const summary = useRecordingSummary();
  const [selected, setSelected] = useState(camera.recording_profile);
  const save = useMutation({
    mutationFn: (id: string | null) => api.setRecordingProfile(camera.id, id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["recording-profiles"] });
      onClose();
    },
  });
  const chosen = profiles.find((p) => p.id === selected);
  const isAdmin = useIsAdmin();
  const makeDefault = useMutation({
    mutationFn: (id: string) => api.setDefaultRecordingProfile(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["recording-profiles"] });
    },
  });

  return (
    <>
      <DialogContent className="form-stack">
        {tabs}
        <Body1 className="muted">{t("recording.help")}</Body1>
        <QueryState error={list.error} loading={list.isPending} />
        <RadioGroup value={selected} onChange={(_, d) => setSelected(d.value)}>
          {profiles.map((p) => (
            <Radio
              key={p.id}
              value={p.id}
              label={
                <span>
                  {p.display_name}
                  {p.default && <Caption1 className="muted"> ({t("camera.default")})</Caption1>}
                  <Caption1 as="span" className="muted option-hint">
                    {summary(p)}
                  </Caption1>
                </span>
              }
            />
          ))}
        </RadioGroup>
        {isAdmin && chosen && !chosen.default && (
          <div className="row wrap">
            <Button
              size="small"
              disabled={makeDefault.isPending}
              onClick={() => makeDefault.mutate(chosen.id)}
            >
              {t("recording.makeDefault", { name: chosen.display_name })}
            </Button>
            <Caption1 className="muted">{t("recording.makeDefaultHint")}</Caption1>
          </div>
        )}
        {makeDefault.error && (
          <MessageBar intent="error">
            <MessageBarBody>{makeDefault.error.message}</MessageBarBody>
          </MessageBar>
        )}
        {chosen?.transcode && (
          <MessageBar>
            <MessageBarBody>
              {t("recording.transcodeNote")}
              {chosen.codec === "h265" && <> {t("recording.h265Note")}</>}
            </MessageBarBody>
          </MessageBar>
        )}
        <Caption1 className="muted">
          {camera.recording_profile_source === "ui"
            ? t("cameras.profilesFromUi")
            : camera.recording_profile_source === "config"
              ? t("cameras.profilesFromConfig")
              : t("recording.fromDefault")}
        </Caption1>
        {save.error && (
          <MessageBar intent="error">
            <MessageBarBody>{save.error.message}</MessageBarBody>
          </MessageBar>
        )}
      </DialogContent>
      <DialogActions>
        {camera.recording_profile_source === "ui" && (
          <Button disabled={save.isPending} onClick={() => save.mutate(null)}>
            {t("cameras.profilesReset")}
          </Button>
        )}
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          appearance="primary"
          disabled={save.isPending}
          onClick={() => save.mutate(selected)}
        >
          {t("common.save")}
        </Button>
      </DialogActions>
    </>
  );
}

function DetectionPanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const { data: profiles = [] } = useProfiles();
  const [selected, setSelected] = useState<string[]>(camera.profiles);
  const save = useMutation({
    mutationFn: (ids: string[] | null) => api.setCameraProfiles(camera.id, ids),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["profiles"] });
      onClose();
    },
  });
  const toggle = (id: string, on: boolean) =>
    setSelected((cur) => (on ? [...cur, id] : cur.filter((x) => x !== id)));

  return (
    <>
      <DialogContent className="form-stack">
        {tabs}
        <Body1 className="muted">{t("cameras.profilesHelp")}</Body1>
        {profiles
          .filter((p) => p.enabled)
          .map((p) => (
            <Checkbox
              key={p.id}
              checked={selected.includes(p.id)}
              onChange={(_, d) => toggle(p.id, d.checked === true)}
              label={
                <span>
                  {p.display_name} <Caption1 className="muted">({p.targets.join(", ")})</Caption1>
                </span>
              }
            />
          ))}
        <Caption1 className="muted">
          {camera.profiles_source === "ui"
            ? t("cameras.profilesFromUi")
            : camera.profiles_source === "config"
              ? t("cameras.profilesFromConfig")
              : t("cameras.profilesFromDefault")}
        </Caption1>
        {save.error && (
          <MessageBar intent="error">
            <MessageBarBody>{save.error.message}</MessageBarBody>
          </MessageBar>
        )}
      </DialogContent>
      <DialogActions>
        {camera.profiles_source === "ui" && (
          <Button disabled={save.isPending} onClick={() => save.mutate(null)}>
            {t("cameras.profilesReset")}
          </Button>
        )}
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          appearance="primary"
          disabled={save.isPending}
          onClick={() => save.mutate(selected)}
        >
          {t("common.save")}
        </Button>
      </DialogActions>
    </>
  );
}
