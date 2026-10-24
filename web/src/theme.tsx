import {
  createDarkTheme,
  createLightTheme,
  FluentProvider,
  Menu,
  MenuButton,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
  type BrandVariants,
  type Theme,
} from "@fluentui/react-components";
import {
  DarkTheme20Regular,
  WeatherMoon20Regular,
  WeatherSunny20Regular,
} from "@fluentui/react-icons";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { useI18n } from "./i18n";
import { useMediaQuery } from "./useMediaQuery";

// Fox orange brand ramp (10 = darkest, 160 = lightest), built around #ff7a1a.
const fox: BrandVariants = {
  10: "#080401",
  20: "#24130a",
  30: "#3c1c0c",
  40: "#52240d",
  50: "#692c0d",
  60: "#80350c",
  70: "#983e0a",
  80: "#b14807",
  90: "#ca5203",
  100: "#e45f05",
  110: "#ff7a1a",
  120: "#ff8f42",
  130: "#ffa466",
  140: "#ffb98a",
  150: "#ffcdad",
  160: "#ffe2d1",
};

export const darkTheme: Theme = {
  ...createDarkTheme(fox),
  // Dark themes read the brand from lighter shades so it stays legible.
  colorBrandForeground1: fox[110],
  colorBrandForeground2: fox[120],
  colorBrandForegroundLink: fox[120],
  colorBrandForegroundLinkHover: fox[130],
  colorBrandBackground: fox[100],
  colorBrandBackgroundHover: fox[110],
  colorBrandBackgroundPressed: fox[90],
  colorCompoundBrandStroke: fox[110],
  colorCompoundBrandBackground: fox[110],
};

// Fluent's light defaults use the darker shades (80 and below), which keep text contrast on white.
export const lightTheme: Theme = createLightTheme(fox);

export type ThemeMode = "system" | "light" | "dark";
type Resolved = "light" | "dark";

export const THEME_MODES: ThemeMode[] = ["system", "light", "dark"];
const STORAGE_KEY = "media-streamer.theme";
/** Browser/OS chrome colour (<meta name="theme-color">). */
const CHROME_COLOR: Record<Resolved, string> = { light: "#ffffff", dark: "#171b21" };

function initialMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

/** Page background outside the app root (overscroll), form controls and browser chrome. */
function applyResolved(r: Resolved) {
  const root = document.documentElement;
  root.dataset.theme = r;
  root.style.colorScheme = r;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", CHROME_COLOR[r]);
}

interface ThemeModeContext {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
}

const Ctx = createContext<ThemeModeContext | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(initialMode);
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const resolved: Resolved = mode === "system" ? (prefersDark ? "dark" : "light") : mode;
  useEffect(() => applyResolved(resolved), [resolved]);

  const setMode = (m: ThemeMode) => {
    if (m === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, m);
    setModeState(m);
  };
  return (
    // React Compiler memoizes this value, so consumers only re-render when `mode` changes.
    // oxlint-disable-next-line react/jsx-no-constructed-context-values
    <Ctx.Provider value={{ mode, setMode }}>
      <FluentProvider theme={resolved === "dark" ? darkTheme : lightTheme}>
        {children}
      </FluentProvider>
    </Ctx.Provider>
  );
}

export function useThemeMode(): ThemeModeContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useThemeMode() outside <ThemeProvider>");
  return ctx;
}

const ICONS: Record<ThemeMode, ReactElement> = {
  system: <DarkTheme20Regular />,
  light: <WeatherSunny20Regular />,
  dark: <WeatherMoon20Regular />,
};

export function ThemeMenu({ className }: { className?: string }) {
  const { mode, setMode } = useThemeMode();
  const { t } = useI18n();
  return (
    <Menu
      checkedValues={{ theme: [mode] }}
      onCheckedValueChange={(_, d) => d.checkedItems[0] && setMode(d.checkedItems[0] as ThemeMode)}
    >
      <MenuTrigger disableButtonEnhancement>
        <MenuButton
          className={className}
          appearance="subtle"
          icon={ICONS[mode]}
          aria-label={t("common.theme")}
        />
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          {THEME_MODES.map((m) => (
            <MenuItemRadio key={m} name="theme" value={m} icon={ICONS[m]}>
              {t(`theme.${m}`)}
            </MenuItemRadio>
          ))}
        </MenuList>
      </MenuPopover>
    </Menu>
  );
}
