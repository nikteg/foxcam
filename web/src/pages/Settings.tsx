import { Card, CardHeader, Divider, Text, Title3 } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useIsAdmin, useMe } from "../auth";
import { AppearanceSection } from "../components/AppearanceSection";
import { MaintenanceSection } from "../components/MaintenanceSection";
import { Notifications } from "../components/Notifications";
import {
  DEFAULT_RECORDING,
  RuntimeSettingsSection,
  type SettingsGroup,
} from "../components/RuntimeSettings";
import { StorageCard } from "../components/StorageCard";
import { UsersSection } from "../components/UsersSection";
import {
  Fact,
  Facts,
  InfoBanner,
  PageTabs,
  PageTitle,
  QueryState,
  useSubPage,
} from "../components/ui";
import { Navigate } from "react-router";

import { useI18n } from "../i18n";

// Language and theme are in the top bar; everything here is for admins.
type SubPage =
  | "general"
  | "detection"
  | "retention"
  | "users"
  | "notifications"
  | "storage"
  | "configuration";

// The tunables on each tab (server: runtime_settings.EDITABLE).
const GENERAL: SettingsGroup[] = [
  {
    title: "runtime.group.recording",
    keys: ["public_url", "segment_seconds", DEFAULT_RECORDING],
  },
  {
    title: "runtime.group.archiving",
    keys: ["compaction.enabled", "compaction.delay_minutes"],
  },
  {
    title: "runtime.group.live",
    keys: [
      "mediamtx.low_data.enabled",
      "mediamtx.low_data.height",
      "mediamtx.low_data.fps",
      "mediamtx.low_data.bitrate_kbit",
    ],
  },
];
const DETECTION: SettingsGroup[] = [
  {
    title: "runtime.group.frames",
    keys: [
      "processing.sample_fps",
      "processing.max_width",
      "processing.concurrency",
      "processing.night_saturation_threshold",
    ],
  },
  {
    title: "runtime.group.detections",
    keys: ["processing.keep_non_targets_from"],
  },
  {
    title: "runtime.group.events",
    keys: [
      "events.pre_roll_seconds",
      "events.post_roll_seconds",
      "events.merge_gap_seconds",
      "events.settle_seconds",
    ],
  },
  {
    title: "runtime.group.queue",
    keys: ["queue.lease_seconds", "queue.max_attempts", "queue.retry_backoff_seconds"],
  },
];
const RETENTION: SettingsGroup[] = [
  {
    keys: [
      "retention.segments_days",
      "retention.events_days",
      "retention.unprocessed_max_days",
      "retention.max_disk_gb",
    ],
  },
];

const isObject = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);

/** One configuration value, nested objects and lists included (read-only view). */
function ConfigValue({ value }: { value: unknown }) {
  const { t } = useI18n();
  if (value === null || value === undefined || value === "") {
    return <span className="muted">–</span>;
  }
  if (typeof value === "boolean") return <>{value ? t("common.yes") : t("common.no")}</>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">–</span>;
    if (value.every((v) => !isObject(v) && !Array.isArray(v))) {
      return (
        <span className="row wrap tags">
          {value.map((v, i) => (
            // oxlint-disable-next-line react/no-array-index-key -- plain values can repeat
            <code key={i} className="config-chip">
              {String(v)}
            </code>
          ))}
        </span>
      );
    }
    return (
      <div className="config-list">
        {value.map((v, i) => (
          // oxlint-disable-next-line react/no-array-index-key -- list order is the identity
          <div key={i} className="config-item">
            <ConfigValue value={v} />
          </div>
        ))}
      </div>
    );
  }
  if (isObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <span className="muted">–</span>;
    return (
      <Facts className="config-object">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <Fact label={k} className={isObject(v) || Array.isArray(v) ? undefined : "mono"}>
              <ConfigValue value={v} />
            </Fact>
          </div>
        ))}
      </Facts>
    );
  }
  return <>{String(value)}</>;
}

export function Settings() {
  const isAdmin = useIsAdmin();
  const me = useMe();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.settings,
    enabled: isAdmin,
  });
  const { t, rich } = useI18n();
  const cfg = settings.data;
  const ids: SubPage[] = [
    "general",
    "detection",
    "retention",
    ...(me?.enabled ? (["users"] as const) : []),
    "notifications",
    "storage",
    "configuration",
  ];
  const tab = useSubPage("/settings", ids);
  const label: Record<SubPage, string> = {
    general: t("settings.general"),
    detection: t("settings.detection"),
    retention: t("retention.title"),
    users: t("users.title"),
    notifications: t("settings.notifications"),
    storage: t("settings.storage"),
    configuration: t("settings.configuration"),
  };

  if (!isAdmin) return <Navigate to="/" replace />;
  return (
    <>
      <PageTitle title={t("nav.settings")} />
      {ids.length > 1 && (
        <PageTabs
          base="/settings"
          current={tab}
          tabs={ids.map((id) => ({ id, label: label[id] }))}
        />
      )}

      {/* General has two parts: this browser's choices, then the server's settings (what the
          banner below is about). */}
      {tab === "general" && (
        <>
          <AppearanceSection part />
          <Divider className="settings-divider" />
          <Title3 as="h2" className="settings-part">
            {t("settings.serverPart")}
          </Title3>
        </>
      )}
      {(tab === "general" || tab === "detection" || tab === "retention") && (
        <InfoBanner id="runtime-settings">
          {rich("runtime.intro", { file: <code>config.yaml</code> })}
        </InfoBanner>
      )}
      {tab === "general" && <RuntimeSettingsSection groups={GENERAL} />}
      {tab === "detection" && (
        <>
          <RuntimeSettingsSection groups={DETECTION} />
          <MaintenanceSection />
        </>
      )}
      {tab === "retention" && <RuntimeSettingsSection groups={RETENTION} showDisk />}
      {tab === "users" && <UsersSection />}
      {tab === "notifications" && <Notifications />}
      {tab === "storage" && <StorageCard />}
      {tab === "configuration" && (
        <>
          <InfoBanner id="configuration">
            {rich("settings.configIntro", { file: <code>config.yaml</code> })}
          </InfoBanner>
          <QueryState error={settings.error} loading={settings.isPending} />
        </>
      )}
      {tab === "configuration" && cfg && (
        <div className="cards">
          {Object.entries(cfg)
            .filter(([key]) => key !== "cameras")
            .map(([key, value]) => (
              <Card key={key} size="small">
                <CardHeader header={<Text weight="semibold">{key}</Text>} />
                {isObject(value) ? (
                  <ConfigValue value={value} />
                ) : (
                  <div className={Array.isArray(value) ? undefined : "mono"}>
                    <ConfigValue value={value} />
                  </div>
                )}
              </Card>
            ))}
        </div>
      )}
    </>
  );
}
