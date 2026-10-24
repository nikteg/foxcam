// One camera card for the Status page and the Cameras page: snapshot, status and small
// buttons (Settings for editors and owners); the picture opens the live view. ``manage`` adds what the Cameras page needs:
// the camera's details, the recording switch and censor areas.
import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  CardPreview,
  Switch,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import { Folder20Regular, Play20Filled, Settings20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, snapshotUrl, type Camera, type CameraSource } from "../api";
import { hasRole, useMe } from "../auth";
import { useI18n } from "../i18n";
import { useProfileName } from "./ProfileSelect";
import { useRoleName } from "./ShareDialog";
import { ConfigTag, Dot, Fact, Facts, Light, Tag, Time, useRouterHref, useSourceLabel } from "./ui";

export function sourceDetail(source: CameraSource, synthetic: string): string {
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

function useRecordingProfileName(): (id: string) => string {
  const { data = [] } = useQuery({
    queryKey: ["recording-profiles"],
    queryFn: api.recordingProfiles,
  });
  return (id) => data.find((p) => p.id === id)?.display_name ?? id;
}

export function CameraCard({
  camera: c,
  refreshKey,
  manage = false,
  onSettings,
  onLive,
}: {
  camera: Camera;
  /** Changes re-request the snapshot (with the page's poll). */
  refreshKey: number;
  /** Cameras page: details, recording switch and censor areas. */
  manage?: boolean;
  /** Opens the camera's settings (shown to editors and owners). */
  onSettings?: () => void;
  /** Opens the live view (the picture is its button). */
  onLive: () => void;
}) {
  const { t } = useI18n();
  const me = useMe();
  const routerHref = useRouterHref();
  const sourceLabel = useSourceLabel();
  const profileName = useProfileName();
  const recordingName = useRecordingProfileName();
  const roleName = useRoleName();
  const client = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.updateCamera(c.id, { enabled }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["status"] });
    },
  });
  const canEdit = hasRole(c, "editor");
  const isOwner = hasRole(c, "owner");
  const editable = c.managed === "ui" && canEdit;

  return (
    <Card className="camera-card">
      <CardHeader
        header={
          <span className="row">
            <Dot ok={c.online} />
            <Text weight="semibold">{c.name}</Text>
            <Caption1 className="muted">{sourceLabel(c.source)}</Caption1>
          </span>
        }
        action={
          // Fixed-height row; the light tag keeps its place (hidden) until the first
          // segment has been tagged, so the header never changes height.
          <span className="card-status">
            {c.private && <Tag kind="private">{t("state.private")}</Tag>}
            <span className={c.last_is_night === null ? "placeholder" : undefined}>
              <Tag>
                <Light night={c.last_is_night ?? false} long small />
              </Tag>
            </span>
          </span>
        }
      />
      <CardPreview>
        <button
          type="button"
          className="thumb-button"
          aria-label={t("dashboard.openLive", { name: c.name })}
          onClick={onLive}
        >
          <img
            className="thumb"
            loading="lazy"
            src={`${snapshotUrl(c.id)}?t=${refreshKey}`}
            alt={t("dashboard.snapshotAlt", { name: c.name })}
            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
          />
          <span className="play-overlay" aria-hidden="true">
            <Play20Filled />
          </span>
        </button>
      </CardPreview>

      {manage && (c.managed === "config" || (me?.enabled && c.role !== "owner") || !c.enabled) && (
        <span className="row wrap tags">
          {c.managed === "config" && <ConfigTag />}
          {me?.enabled && c.role !== "owner" && <Tag>{roleName(c.role)}</Tag>}
          {!c.enabled && <Tag kind="warn">{t("cameras.disabled")}</Tag>}
        </span>
      )}
      <Facts>
        {manage && (
          <>
            <Fact label={t("cameras.source")} className="mono">
              {sourceDetail(c.source_config, t("cameras.synthetic"))}
            </Fact>
          </>
        )}
        <Fact label={t("camera.lastSegment")}>
          <Time iso={c.last_segment_at} />
        </Fact>
        <Fact label={t("camera.segmentLength")}>
          {c.segment_seconds} s
          {manage && c.segment_seconds_override === null && (
            <span className="muted"> ({t("camera.default")})</span>
          )}
        </Fact>
        {manage ? (
          <>
            <Fact label={t("recording.title")}>
              <span className="row wrap tags">
                {recordingName(c.recording_profile)}
                {c.burn_masks && c.rects.length > 0 && <Tag>{t("recording.masksBurnedIn")}</Tag>}
              </span>
            </Fact>
            <Fact label={t("camera.censorAreas")}>{c.rects.length}</Fact>
            <Fact label={t("profiles.title")}>
              {c.profiles.length ? (
                <span className="row wrap tags">
                  {c.profiles.map((id) => (
                    <Tag key={id}>{profileName(id)}</Tag>
                  ))}
                </span>
              ) : (
                <span className="muted">{t("cameras.noProfiles")}</span>
              )}
            </Fact>
          </>
        ) : (
          <>
            <Fact label={t("camera.viewers")}>{c.readers}</Fact>
          </>
        )}
      </Facts>
      {manage && (
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
      )}
      <CardFooter className="wrap">
        <Button
          as="a"
          size="small"
          icon={<Folder20Regular />}
          {...routerHref(`/recordings?camera=${c.id}`)}
        >
          {t("nav.recordings")}
        </Button>
        {onSettings && (canEdit || isOwner) && (
          <Button size="small" icon={<Settings20Regular />} onClick={onSettings}>
            {t("cameras.settings")}
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}
