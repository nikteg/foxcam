// Language and theme: saved in this browser only. In Settings → General and on the
// Account page (Settings is for admins); on phones the top bar has no room for them.
import { Field, Select, Subtitle2 } from "@fluentui/react-components";

import { LANGUAGE_NAMES, useI18n, type Lang } from "../i18n";
import { THEME_MODES, useThemeMode, type ThemeMode } from "../theme";

export function AppearanceSection() {
  const { t, lang, setLang } = useI18n();
  const { mode, setMode } = useThemeMode();
  return (
    <section className="form-stack appearance">
      <Subtitle2 as="h2" className="panel-heading">
        {t("appearance.title")}
      </Subtitle2>
      <Field label={t("common.language")}>
        <Select value={lang} onChange={(_, d) => setLang(d.value as Lang)}>
          {(Object.keys(LANGUAGE_NAMES) as Lang[]).map((l) => (
            <option key={l} value={l}>
              {LANGUAGE_NAMES[l]}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("common.theme")} hint={t("appearance.hint")}>
        <Select value={mode} onChange={(_, d) => setMode(d.value as ThemeMode)}>
          {THEME_MODES.map((m) => (
            <option key={m} value={m}>
              {t(`theme.${m}`)}
            </option>
          ))}
        </Select>
      </Field>
    </section>
  );
}
