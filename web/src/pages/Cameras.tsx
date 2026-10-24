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
  MessageBarBody,
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
  Edit20Regular,
  People20Regular,
  ScanObject20Regular,
  EyeOff20Regular,
  Video20Regular,
  VideoClip20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api, type Camera, type CameraSource, type RecordingProfile } from "../api";
import { hasRole, useMe } from "../auth";
import { AddCameraDialog } from "../components/AddCameraDialog";
import { ShareDialog, useRoleName } from "../components/ShareDialog";
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
  const [editing, setEditing] = useState<Camera | null>(null);
  const [removing, setRemoving] = useState<Camera | null>(null);
  const [choosing, setChoosing] = useState<Camera | null>(null);
  const [recordingFor, setRecordingFor] = useState<Camera | null>(null);
  const [sharing, setSharing] = useState<Camera | null>(null);
  const me = useMe();
  const isAdmin = me?.is_admin ?? false;
  // Sub-pages with their own URL (links work even while parts are still loading).
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const tab = !isAdmin
    ? "cameras"
    : pathname.endsWith("/profiles")
      ? "profiles"
      : pathname.endsWith("/integrations")
        ? "integrations"
        : "cameras";

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
        <TabList
          className="block"
          selectedValue={tab}
          onTabSelect={(_, d) =>
            void navigate(d.value === "cameras" ? "/cameras" : `/cameras/${String(d.value)}`)
          }
        >
          <Tab value="cameras">{t("nav.cameras")}</Tab>
          <Tab value="profiles">{t("profiles.title")}</Tab>
          <Tab value="integrations">{t("cameras.integrations")}</Tab>
        </TabList>
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
              <CameraCard
                key={c.id}
                camera={c}
                onEdit={() => setEditing(c)}
                onRemove={() => setRemoving(c)}
                onProfiles={() => setChoosing(c)}
                onRecording={() => setRecordingFor(c)}
                onShare={me?.enabled ? () => setSharing(c) : undefined}
              />
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
      {editing && <EditCameraDialog camera={editing} onClose={() => setEditing(null)} />}
      {removing && <RemoveCameraDialog camera={removing} onClose={() => setRemoving(null)} />}
      {choosing && <CameraProfilesDialog camera={choosing} onClose={() => setChoosing(null)} />}
      {recordingFor && (
        <RecordingProfileDialog camera={recordingFor} onClose={() => setRecordingFor(null)} />
      )}
      {sharing && <ShareDialog camera={sharing} onClose={() => setSharing(null)} />}
    </>
  );
}

function CameraCard({
  camera: c,
  onEdit,
  onRemove,
  onProfiles,
  onRecording,
  onShare,
}: {
  camera: Camera;
  onEdit: () => void;
  onRemove: () => void;
  onProfiles: () => void;
  onRecording: () => void;
  /** Undefined when accounts are turned off. */
  onShare?: () => void;
}) {
  const { t } = useI18n();
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
            {onShare && c.role !== "owner" && <Tag>{roleName(c.role)}</Tag>}
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
        {canEdit && (
          <>
            <Button
              as="a"
              size="small"
              icon={<EyeOff20Regular />}
              {...routerHref(`/cameras/${c.id}/mask`)}
            >
              {t("camera.censorAreas")}
            </Button>
            <Button size="small" icon={<ScanObject20Regular />} onClick={onProfiles}>
              {t("cameras.detect")}
            </Button>
            <Button size="small" icon={<VideoClip20Regular />} onClick={onRecording}>
              {t("recording.title")}
            </Button>
          </>
        )}
        {onShare && isOwner && (
          <Button size="small" icon={<People20Regular />} onClick={onShare}>
            {t("share.button")}
          </Button>
        )}
        {editable && (
          <Button size="small" icon={<Edit20Regular />} onClick={onEdit}>
            {t("common.edit")}
          </Button>
        )}
        {editable && isOwner && (
          <Button size="small" icon={<Delete20Regular />} onClick={onRemove}>
            {t("common.remove")}
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}

function EditCameraDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [name, setName] = useState(camera.name);
  const [segment, setSegment] = useState(camera.segment_seconds_override?.toString() ?? "");
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
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <DialogBody>
            <DialogTitle>{t("cameras.editTitle", { name: camera.name })}</DialogTitle>
            <DialogContent className="form-stack">
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
              {save.error && (
                <MessageBar intent="error">
                  <MessageBarBody>{save.error.message}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button type="submit" appearance="primary" disabled={!name || save.isPending}>
                {t("common.save")}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
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

function RecordingProfileDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
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

  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("recording.dialogTitle", { name: camera.name })}</DialogTitle>
          <DialogContent className="form-stack">
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
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function CameraProfilesDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
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
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("cameras.profilesTitle", { name: camera.name })}</DialogTitle>
          <DialogContent className="form-stack">
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
                      {p.display_name}{" "}
                      <Caption1 className="muted">({p.targets.join(", ")})</Caption1>
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
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function RemoveCameraDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteCamera(camera.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["status"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("cameras.removeTitle", { name: camera.name })}</DialogTitle>
          <DialogContent>
            <Body1>{t("cameras.removeBody")}</Body1>
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
