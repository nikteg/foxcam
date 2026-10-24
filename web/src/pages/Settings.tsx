import { Card, CardHeader, Text } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useIsAdmin, useMe } from "../auth";
import { Notifications } from "../components/Notifications";
import { RetentionSection } from "../components/RetentionSection";
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
type SubPage = "retention" | "users" | "notifications" | "storage" | "configuration";

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
    "retention",
    ...(me?.enabled ? (["users"] as const) : []),
    "notifications",
    "storage",
    "configuration",
  ];
  const tab = useSubPage("/settings", ids);
  const label: Record<SubPage, string> = {
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

      {tab === "retention" && <RetentionSection />}
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
