import {
  Button,
  Card,
  CardHeader,
  Field,
  Radio,
  RadioGroup,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import { Camera20Regular } from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { StorageCard } from "../components/StorageCard";
import { PageHeader, QueryState, useRouterHref } from "../components/ui";
import { LANGUAGE_NAMES, useI18n, type Lang } from "../i18n";
import { THEME_MODES, useThemeMode, type ThemeMode } from "../theme";

const show = (v: unknown) =>
  v === null || v === undefined ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v);

export function Settings() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const routerHref = useRouterHref();
  const { t, rich, lang, setLang } = useI18n();
  const { mode, setMode } = useThemeMode();
  const cfg = settings.data;

  return (
    <>
      <PageHeader title={t("nav.settings")} />

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

      <Subtitle2 as="h2" className="section">
        {t("nav.cameras")}
      </Subtitle2>
      <Button as="a" icon={<Camera20Regular />} {...routerHref("/cameras")}>
        {t("settings.manageCameras")}
      </Button>

      <Subtitle2 as="h2" className="section">
        {t("settings.storage")}
      </Subtitle2>
      <StorageCard />

      <Subtitle2 as="h2" className="section">
        {t("settings.configuration")}
      </Subtitle2>
      <p className="muted">{rich("settings.configIntro", { file: <code>config.yaml</code> })}</p>
      <QueryState error={settings.error} loading={settings.isPending} />
      {cfg && (
        <div className="cards">
          {Object.entries(cfg)
            .filter(([key]) => key !== "cameras")
            .map(([key, value]) => (
              <Card key={key} size="small">
                <CardHeader header={<Text weight="semibold">{key}</Text>} />
                {value && typeof value === "object" && !Array.isArray(value) ? (
                  <dl className="facts">
                    {Object.entries(value).map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt>{k}</dt>
                        <dd className="mono">{show(v)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <div className="mono">{show(value)}</div>
                )}
              </Card>
            ))}
        </div>
      )}
    </>
  );
}
