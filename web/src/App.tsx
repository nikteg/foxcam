import { Button, Caption1, Tab, TabList, Text, Title2 } from "@fluentui/react-components";
import {
  AnimalCat24Regular,
  Camera24Regular,
  DataBarVertical24Regular,
  Folder24Regular,
  Search24Regular,
  Server24Regular,
  Settings24Regular,
  Video24Regular,
} from "@fluentui/react-icons";
import type { ReactElement } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";

import { RouterLink, usePageTitle } from "./components/ui";
import { LanguageMenu, useI18n, type MessageKey } from "./i18n";

interface NavItem {
  to: string;
  label: MessageKey;
  short: MessageKey;
  icon: ReactElement;
}

// `short` labels are used in the phone bottom bar, where Settings moves to the header.
const TABS: NavItem[] = [
  { to: "/", label: "nav.status", short: "nav.status", icon: <DataBarVertical24Regular /> },
  { to: "/live", label: "nav.live", short: "nav.live", icon: <Video24Regular /> },
  { to: "/events", label: "nav.events", short: "nav.eventsShort", icon: <AnimalCat24Regular /> },
  {
    to: "/detections",
    label: "nav.detections",
    short: "nav.detectionsShort",
    icon: <Search24Regular />,
  },
  {
    to: "/recordings",
    label: "nav.recordings",
    short: "nav.recordingsShort",
    icon: <Folder24Regular />,
  },
  { to: "/queue", label: "nav.queue", short: "nav.queueShort", icon: <Server24Regular /> },
];

// Shown after the main tabs; the bottom bar (tablets/phones) includes them too.
const EXTRA_TABS: NavItem[] = [
  { to: "/cameras", label: "nav.cameras", short: "nav.cameras", icon: <Camera24Regular /> },
];

function currentTab(pathname: string): string {
  if (pathname === "/") return "/";
  if (pathname.startsWith("/settings")) return "/settings";
  if (pathname === "/cameras" || pathname === "/profiles") return "/cameras";
  return TABS.find((t) => t.to !== "/" && pathname.startsWith(t.to))?.to ?? "";
}

export function Layout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const selected = currentTab(pathname);
  const { t } = useI18n();

  return (
    <>
      <header className="topbar">
        <RouterLink to="/" className="brand">
          <Text weight="semibold" size={500}>
            🦊 Media Streamer
          </Text>
        </RouterLink>
        <TabList
          className="top-tabs"
          selectedValue={selected}
          onTabSelect={(_, d) => navigate(String(d.value))}
          size="medium"
        >
          {TABS.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
          {EXTRA_TABS.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
          <Tab value="/settings" icon={<Settings24Regular />}>
            {t("nav.settings")}
          </Tab>
        </TabList>
        {/* Right-aligned header actions (settings shortcut only shown with the bottom bar). */}
        <div className="topbar-actions">
          <LanguageMenu className="language-menu" />
          <Button
            className="settings-button"
            appearance={selected === "/settings" ? "secondary" : "subtle"}
            icon={<Settings24Regular />}
            aria-label={t("nav.settings")}
            onClick={() => navigate("/settings")}
          />
        </div>
      </header>

      <main>
        <Outlet />
      </main>

      <nav className="bottom-tabs" aria-label={t("nav.main")}>
        {[...TABS, ...EXTRA_TABS].map((tab) => (
          <button
            key={tab.to}
            type="button"
            className={selected === tab.to ? "active" : undefined}
            aria-current={selected === tab.to ? "page" : undefined}
            onClick={() => navigate(tab.to)}
          >
            {tab.icon}
            <Caption1>{t(tab.short)}</Caption1>
          </button>
        ))}
      </nav>
    </>
  );
}

export function NotFound() {
  const { t } = useI18n();
  usePageTitle(t("notFound.title"));
  return (
    <>
      <Title2 as="h1">{t("notFound.title")}</Title2>
      <p>
        <RouterLink to="/">{t("notFound.back")}</RouterLink>
      </p>
    </>
  );
}
