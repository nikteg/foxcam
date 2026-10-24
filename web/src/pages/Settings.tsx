import {
  Card,
  CardHeader,
  Field,
  Radio,
  RadioGroup,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useIsAdmin, useMe } from "../auth";
import { Notifications } from "../components/Notifications";
import { StorageCard } from "../components/StorageCard";
import { UsersSection } from "../components/UsersSection";
import { PageHeader, PageTabs, QueryState, useSubPage } from "../components/ui";
import { LANGUAGE_NAMES, useI18n, type Lang } from "../i18n";
import { THEME_MODES, useThemeMode, type ThemeMode } from "../theme";

type SubPage = "general" | "users" | "notifications" | "storage" | "configuration";

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
      <dl className="facts config-object">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt>{k}</dt>
            <dd className={isObject(v) || Array.isArray(v) ? undefined : "mono"}>
              <ConfigValue value={v} />
            </dd>
          </div>
        ))}
      </dl>
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
  const { t, rich, lang, setLang } = useI18n();
  const { mode, setMode } = useThemeMode();
  const cfg = settings.data;
  const ids: SubPage[] = [
    "general",
    ...(isAdmin && me?.enabled ? (["users"] as const) : []),
    ...(isAdmin ? (["notifications", "storage", "configuration"] as const) : []),
  ];
  const tab = useSubPage("/settings", ids);
  const label: Record<SubPage, string> = {
    general: t("settings.general"),
    users: t("users.title"),
    notifications: t("settings.notifications"),
    storage: t("settings.storage"),
    configuration: t("settings.configuration"),
  };

  return (
    <>
      <PageHeader title={t("nav.settings")} />
      {ids.length > 1 && (
        <PageTabs
          base="/settings"
          current={tab}
          tabs={ids.map((id) => ({ id, label: label[id] }))}
        />
      )}

      {tab === "general" && (
        <>
          <Subtitle2 as="h2" className="section">
            {t("settings.language")}
          </Subtitle2>
          <Field hint={t("settings.languageHint")}>
            <RadioGroup
              layout="horizontal"
              value={lang}
              onChange={(_, d) => setLang(d.value as Lang)}
              aria-label={t("settings.language")}
            >
              {(Object.keys(LANGUAGE_NAMES) as Lang[]).map((l) => (
                <Radio key={l} value={l} label={LANGUAGE_NAMES[l]} />
              ))}
            </RadioGroup>
          </Field>

          <Subtitle2 as="h2" className="section">
            {t("settings.theme")}
          </Subtitle2>
          <Field hint={t("settings.themeHint")}>
            <RadioGroup
              layout="horizontal"
              value={mode}
              onChange={(_, d) => setMode(d.value as ThemeMode)}
              aria-label={t("settings.theme")}
            >
              {THEME_MODES.map((m) => (
                <Radio key={m} value={m} label={t(`theme.${m}`)} />
              ))}
            </RadioGroup>
          </Field>
        </>
      )}

      {tab === "users" && <UsersSection />}
      {tab === "notifications" && <Notifications />}
      {tab === "storage" && <StorageCard />}
      {tab === "configuration" && (
        <>
          <p className="muted">
            {rich("settings.configIntro", { file: <code>config.yaml</code> })}
          </p>
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
