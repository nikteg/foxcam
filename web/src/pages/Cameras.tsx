import {
  Body1,
  Subtitle2,
  Button,
  Caption1,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Radio,
  RadioGroup,
  Tab,
  TabList,
  Select,
} from "@fluentui/react-components";
import {
  Add20Regular,
  Delete20Regular,
  EyeOff20Regular,
  Run20Regular,
  WeatherSunny20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { api, type Camera } from "../api";
import { useIsAdmin, hasRole, useMe } from "../auth";
import { AddCameraDialog } from "../components/AddCameraDialog";
import { MaskDrawer } from "./MaskEditor";
import { CameraCard } from "../components/CameraCard";
import { LiveDrawer } from "../components/LiveDrawer";
import { SharePanel } from "../components/ShareDialog";
import { ProfileDialog, ProfilesSection } from "./Profiles";
import { useProfiles } from "../components/ProfileSelect";
import { TapoAccountCard } from "../components/TapoAccount";
import { SunDrawer, sunChartQuery } from "../components/SunDrawer";
import { decimal } from "../format";
import {
  Checkbox,
  Empty,
  InfoBanner,
  PageTabs,
  PageTitle,
  QueryState,
  RouterLink,
  Tag,
  Toolbar,
  useSubPage,
} from "../components/ui";
import { ConfirmPopover } from "../components/ConfirmPopover";
import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";
import { useRecordingProfiles, useRecordingSummary } from "../recordingProfiles";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";
import { CardsSkeleton, ChoicesSkeleton } from "../components/Skeletons";

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
  const [liveFor, setLiveFor] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me?.is_admin ?? false;
  // Sub-pages with their own URL (links work even while parts are still loading).
  const navigate = useNavigate();
  const subPage = useSubPage("/cameras", ["cameras", "profiles", "integrations"] as const);
  const tab = isAdmin ? subPage : "cameras";

  return (
    <>
      <PageTitle title={t("nav.cameras")} />

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

      {tab === "profiles" && (
        <>
          <Toolbar>
            <Button
              appearance="primary"
              icon={<Add20Regular />}
              onClick={() => setAddingProfile(true)}
            >
              {t("profiles.add")}
            </Button>
          </Toolbar>
          <ProfilesSection />
        </>
      )}

      {tab === "cameras" && (
        <>
          {me?.can_add_cameras && (
            <Toolbar>
              <Button appearance="primary" icon={<Add20Regular />} onClick={() => setAdding(true)}>
                {t("addCamera.title")}
              </Button>
            </Toolbar>
          )}
          <InfoBanner id={isAdmin ? "cameras" : "cameras-user"}>
            {isAdmin
              ? rich("cameras.intro", { file: <code>config.yaml</code> })
              : t("cameras.introUser")}
          </InfoBanner>
          <QueryState
            error={cameras.error}
            loading={cameras.isPending}
            skeleton={<CardsSkeleton count={2} />}
          />
          {cameras.data?.length === 0 && <Empty>{t("cameras.none")}</Empty>}
          <div className="cards">
            {cameras.data?.map((c) => (
              <CameraCard
                key={c.id}
                camera={c}
                onLive={() => setLiveFor(c.id)}
                refreshKey={Math.floor(cameras.dataUpdatedAt / 10_000)}
                manage
                onSettings={() => setSettingsFor(c)}
              />
            ))}
          </div>
        </>
      )}

      <AddCameraDialog open={adding} onClose={() => setAdding(false)} />
      {liveFor && (
        <LiveDrawer
          cameras={cameras.data ?? []}
          cameraId={liveFor}
          onClose={() => setLiveFor(null)}
        />
      )}
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

function GeneralPanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const phone = useMediaQuery(PHONE);
  const { t } = useI18n();
  const client = useQueryClient();
  const editable = camera.managed === "ui" && hasRole(camera, "editor");
  const canRemove = camera.managed === "ui" && hasRole(camera, "owner");
  const [name, setName] = useState(camera.name);
  const [segment, setSegment] = useState(camera.segment_seconds_override?.toString() ?? "");
  const tapo = camera.source_config.type === "tapo_cloud" ? camera.source_config : null;
  const [quality, setQuality] = useState<"HD" | "VGA">(tapo?.resolution ?? "HD");
  const [editingMask, setEditingMask] = useState(false);
  const save = useMutation({
    mutationFn: () =>
      api.updateCamera(camera.id, {
        name,
        ...(segment ? { segment_seconds: Number(segment) } : { clear_segment_seconds: true }),
        ...(tapo && quality !== (tapo.resolution ?? "HD") ? { tapo_resolution: quality } : {}),
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
            {tapo && (
              <Field label={t("addCamera.streamQuality")} hint={t("cameras.tapoQualityHint")}>
                <Select value={quality} onChange={(_, d) => setQuality(d.value as "HD" | "VGA")}>
                  <option value="HD">{t("addCamera.qualityHd")}</option>
                  <option value="VGA">{t("addCamera.qualityVga")}</option>
                </Select>
              </Field>
            )}
            <Caption1 className="muted">{t("cameras.changeSourceHint")}</Caption1>
          </>
        ) : (
          <Body1 className="muted">{t("cameras.configHint")}</Body1>
        )}
        <Subtitle2 as="h3" className="panel-heading">
          {t("camera.censorAreas")}
        </Subtitle2>
        <Caption1 className="muted">
          {t("cameras.censorSummary", { n: camera.rects.length })}
          {camera.burn_masks && camera.rects.length > 0 && <> {t("cameras.censorBurned")}</>}
        </Caption1>
        <div>
          <Button size="small" icon={<EyeOff20Regular />} onClick={() => setEditingMask(true)}>
            {t("cameras.editCensorAreas")}
          </Button>
        </div>
        {editingMask && <MaskDrawer cameraId={camera.id} onClose={() => setEditingMask(false)} />}
        {error && (
          <MessageBar intent="error">
            <MessageBarBody>{error.message}</MessageBarBody>
          </MessageBar>
        )}
      </DialogContent>
      <DialogActions>
        {canRemove && (
          <span className="actions-start">
            <ConfirmPopover
              title={t("cameras.removeTitle", { name: camera.name })}
              body={t("cameras.removeBody")}
              onConfirm={() => remove.mutateAsync()}
              align="start"
            >
              {/* Icon only on phones, so Cancel and Save still fit on the row. */}
              <Button
                appearance="subtle"
                className="danger"
                icon={<Delete20Regular />}
                aria-label={phone ? t("cameras.removeCamera") : undefined}
              >
                {phone ? null : t("cameras.removeCamera")}
              </Button>
            </ConfirmPopover>
          </span>
        )}
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
export function CameraSettingsDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
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

function RecordingPanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const { t, rich } = useI18n();
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

  return (
    <>
      <DialogContent className="form-stack">
        {tabs}
        <Body1 className="muted">{t("recording.help")}</Body1>
        <QueryState error={list.error} loading={list.isPending} skeleton={<ChoicesSkeleton />} />
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
        {camera.recording_profile_source !== "ui" && (
          <Caption1 className="muted">
            {camera.recording_profile_source === "config"
              ? t("cameras.profilesFromConfig")
              : t("recording.fromDefault")}
          </Caption1>
        )}
        {isAdmin && (
          <Caption1 className="muted">
            {rich("recording.defaultInSettings", {
              link: <RouterLink to="/settings">{t("recording.settingsLink")}</RouterLink>,
            })}
          </Caption1>
        )}
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
  const [editingMotion, setEditingMotion] = useState(false);
  const [editingSun, setEditingSun] = useState(false);
  useEffect(() => {
    void client.prefetchQuery(sunChartQuery(camera.id));
  }, [client, camera.id]);

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
                <span className="row wrap">
                  {p.display_name}
                  <span className="chips">
                    {p.targets.map((x) => (
                      <Tag key={x}>{x}</Tag>
                    ))}
                  </span>
                </span>
              }
            />
          ))}
        {camera.profiles_source !== "ui" && (
          <Caption1 className="muted">
            {camera.profiles_source === "config"
              ? t("cameras.profilesFromConfig")
              : t("cameras.profilesFromDefault")}
          </Caption1>
        )}
        <Subtitle2 as="h3" className="panel-heading">
          {t("motion.title")}
        </Subtitle2>
        <Caption1 className="muted">
          {t("motion.summary", {
            sensitivity: camera.motion_sensitivity
              ? t(`motion.${camera.motion_sensitivity}`)
              : t("camera.default"),
            n: camera.motion_rects.length,
          })}
        </Caption1>
        <div>
          <Button size="small" icon={<Run20Regular />} onClick={() => setEditingMotion(true)}>
            {t("motion.edit")}
          </Button>
        </div>
        {editingMotion && (
          <MaskDrawer cameraId={camera.id} kind="motion" onClose={() => setEditingMotion(false)} />
        )}
        <Subtitle2 as="h3" className="panel-heading">
          {t("sun.title")}
        </Subtitle2>
        <Caption1 className="muted">
          {camera.sun_limit === null
            ? t("sun.summaryOff")
            : t("sun.summary", { limit: decimal(camera.sun_limit) })}
        </Caption1>
        <div>
          <Button size="small" icon={<WeatherSunny20Regular />} onClick={() => setEditingSun(true)}>
            {t("sun.edit")}
          </Button>
        </div>
        {editingSun && <SunDrawer camera={camera} onClose={() => setEditingSun(false)} />}
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
