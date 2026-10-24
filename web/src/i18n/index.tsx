import {
  Menu,
  MenuButton,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
} from "@fluentui/react-components";
import { LocalLanguage20Regular } from "@fluentui/react-icons";
import { createContext, Fragment, useContext, useState, type ReactNode } from "react";

import { setFormatLocale } from "../format";
import { en, type MessageKey } from "./en";
import { sv } from "./sv";

export type Lang = "en" | "sv";
export type { MessageKey };

const MESSAGES: Record<Lang, Record<MessageKey, string>> = { en, sv };
/** BCP 47 locale used for dates and numbers. */
const LOCALES: Record<Lang, string> = { en: "en-GB", sv: "sv-SE" };
export const LANGUAGE_NAMES: Record<Lang, string> = { en: "English", sv: "Svenska" };
const STORAGE_KEY = "foxcam.lang";

type Vars = Record<string, string | number>;
type RichVars = Record<string, ReactNode>;

export interface I18n {
  lang: Lang;
  locale: string;
  setLang: (lang: Lang) => void;
  /** Translate, replacing `{name}` placeholders. */
  t: (key: MessageKey, vars?: Vars) => string;
  /** Like `t`, but placeholders may be elements (links, <b>, <code>). */
  rich: (key: MessageKey, vars: RichVars) => ReactNode;
}

function initialLang(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "en" || stored === "sv") return stored;
  return navigator.language.toLowerCase().startsWith("sv") ? "sv" : "en";
}

/** `{n:item|items}` picks the singular when `n` is 1 (enough for English and Swedish). */
function plurals(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(
    /\{(\w+):([^|}]*)\|([^}]*)\}/g,
    (m, name: string, one: string, other: string) =>
      name in vars ? (Number(vars[name]) === 1 ? one : other) : m,
  );
}

function interpolate(text: string, vars?: Vars): string {
  if (!vars) return text;
  return plurals(text, vars).replace(/\{(\w+)\}/g, (m, name: string) =>
    name in vars ? String(vars[name]) : m,
  );
}

function richInterpolate(text: string, vars: RichVars): ReactNode {
  // split() with a capture group keeps the placeholder names at odd indices.
  return text.split(/\{(\w+)\}/g).map((part, i) =>
    // oxlint-disable-next-line react/no-array-index-key -- parts of a fixed template string
    i % 2 === 1 ? <Fragment key={i}>{vars[part] ?? `{${part}}`}</Fragment> : part,
  );
}

/** Global side effects of a language choice: number/date locale and <html lang>. */
function applyLang(l: Lang) {
  setFormatLocale(LOCALES[l]);
  document.documentElement.lang = l;
}

const I18nContext = createContext<I18n | null>(null);

function build(lang: Lang, setLang: (l: Lang) => void): I18n {
  const messages = MESSAGES[lang];
  return {
    lang,
    locale: LOCALES[lang],
    setLang,
    t: (key, vars) => interpolate(messages[key] ?? en[key], vars),
    rich: (key, vars) => richInterpolate(messages[key] ?? en[key], vars),
  };
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const l = initialLang();
    applyLang(l);
    return l;
  });
  const setLang = (l: Lang) => {
    localStorage.setItem(STORAGE_KEY, l);
    applyLang(l);
    setLangState(l);
  };
  return (
    // React Compiler memoizes this value, so consumers only re-render when `lang` changes.
    // oxlint-disable-next-line react/jsx-no-constructed-context-values
    <I18nContext.Provider value={build(lang, setLang)}>
      {/* Remount on language change so every formatted date/number is redone too. */}
      <Fragment key={lang}>{children}</Fragment>
    </I18nContext.Provider>
  );
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n() outside <I18nProvider>");
  return ctx;
}

export function LanguageMenu({ className }: { className?: string }) {
  const { lang, setLang, t } = useI18n();
  return (
    <Menu
      checkedValues={{ lang: [lang] }}
      onCheckedValueChange={(_, d) => d.checkedItems[0] && setLang(d.checkedItems[0] as Lang)}
    >
      <MenuTrigger disableButtonEnhancement>
        <MenuButton
          className={className}
          appearance="subtle"
          icon={<LocalLanguage20Regular />}
          aria-label={t("common.language")}
        >
          {lang.toUpperCase()}
        </MenuButton>
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          {(Object.keys(LANGUAGE_NAMES) as Lang[]).map((l) => (
            <MenuItemRadio key={l} name="lang" value={l}>
              {LANGUAGE_NAMES[l]}
            </MenuItemRadio>
          ))}
        </MenuList>
      </MenuPopover>
    </Menu>
  );
}
