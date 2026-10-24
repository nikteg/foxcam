import {
  Button,
  Caption1,
  Menu,
  MenuDivider,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Spinner,
  Tab,
  TabList,
  Text,
  Title2,
} from "@fluentui/react-components";
import {
  Camera24Regular,
  Person20Regular,
  Person24Regular,
  SignOut20Regular,
  DataBarVertical24Regular,
  Folder24Regular,
  Server24Regular,
  Settings24Regular,
  Target24Regular,
} from "@fluentui/react-icons";
import { useLayoutEffect, type ReactElement } from "react";
import { Outlet, useLocation, useNavigate, useNavigationType } from "react-router";

import { api } from "./api";
import { useAuth, useAuthChanged, useUnauthorizedRedirect } from "./auth";
import { RouterLink, usePageTitle } from "./components/ui";
import { AuthScreen } from "./pages/Login";
import { LanguageMenu, useI18n, type MessageKey } from "./i18n";
import { ThemeMenu } from "./theme";

interface NavItem {
  to: string;
  label: MessageKey;
  short: MessageKey;
  icon: ReactElement;
  /** Only shown to admins (the server refuses everyone else anyway). */
  admin?: boolean;
}

// `short` labels are used in the phone bottom bar.
const TABS: NavItem[] = [
  { to: "/", label: "nav.status", short: "nav.status", icon: <DataBarVertical24Regular /> },
  { to: "/events", label: "nav.events", short: "nav.eventsShort", icon: <Target24Regular /> },
  {
    to: "/recordings",
    label: "nav.recordings",
    short: "nav.recordingsShort",
    icon: <Folder24Regular />,
  },
  {
    to: "/jobs",
    label: "nav.queue",
    short: "nav.queueShort",
    icon: <Server24Regular />,
    admin: true,
  },
];

// Shown after the main tabs; the bottom bar (tablets/phones) includes them too.
const EXTRA_TABS: NavItem[] = [
  { to: "/cameras", label: "nav.cameras", short: "nav.cameras", icon: <Camera24Regular /> },
  {
    to: "/settings",
    label: "nav.settings",
    short: "nav.settings",
    icon: <Settings24Regular />,
    admin: true,
  },
];

function currentTab(pathname: string): string {
  if (pathname === "/") return "/";
  if (pathname.startsWith("/cameras")) return "/cameras";
  if (pathname.startsWith("/settings")) return "/settings";
  return TABS.find((t) => t.to !== "/" && pathname.startsWith(t.to))?.to ?? "";
}

/** Signed-in user: account page and sign out. */
function UserMenu() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { data: auth } = useAuth();
  const changed = useAuthChanged();
  // Accounts turned off (auth.enabled: false): nobody signs in.
  if (!auth?.user) return null;
  return (
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <Button
          appearance="subtle"
          icon={<Person24Regular />}
          aria-label={t("account.menu", { name: auth.user.display_name })}
          title={auth.user.display_name}
        />
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          <MenuItem icon={<Person20Regular />} onClick={() => void navigate("/account")}>
            {auth.user.display_name}
          </MenuItem>
          <MenuDivider />
          <MenuItem
            icon={<SignOut20Regular />}
            onClick={() => void api.logout().then(() => changed())}
          >
            {t("account.signOut")}
          </MenuItem>
        </MenuList>
      </MenuPopover>
    </Menu>
  );
}

export function Layout() {
  const { t } = useI18n();
  const auth = useAuth();
  useUnauthorizedRedirect();
  if (auth.isPending) return <Spinner className="block" label={t("common.loading")} />;
  if (auth.error) {
    return <div className="auth-page">{String(auth.error.message ?? auth.error)}</div>;
  }
  if (!auth.data.signed_in) return <AuthScreen />;
  return <AppShell isAdmin={auth.data.is_admin} />;
}

function AppShell({ isAdmin }: { isAdmin: boolean }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const navigationType = useNavigationType();
  const selected = currentTab(pathname);
  // Another page (or sub-page) starts at the top; the router keeps the scroll position
  // otherwise. Back/forward (POP) is left to the browser, and query changes (filters,
  // drawers) do not count.
  useLayoutEffect(() => {
    if (navigationType !== "POP") window.scrollTo(0, 0);
  }, [pathname, navigationType]);
  const { t } = useI18n();
  const tabs = TABS.filter((tab) => isAdmin || !tab.admin);
  const extraTabs = EXTRA_TABS.filter((tab) => isAdmin || !tab.admin);
  // Pressing the open section scrolls back up, like a phone app's tab bar.
  const go = (to: string) => {
    if (to === pathname) window.scrollTo({ top: 0, behavior: "smooth" });
    else void navigate(to);
  };

  return (
    <>
      <header className="topbar">
        <RouterLink to="/" className="brand">
          <Text weight="semibold" size={500}>
            <img className="brand-icon" src="/icon.svg" alt="" />
            Foxcam
          </Text>
        </RouterLink>
        <TabList
          className="top-tabs"
          selectedValue={selected}
          onTabSelect={(_, d) => go(String(d.value))}
          size="medium"
        >
          {tabs.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
          {extraTabs.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
        </TabList>
        {/* Right-aligned header actions. */}
        <div className="topbar-actions">
          <ThemeMenu />
          <LanguageMenu className="language-menu" />
          <UserMenu />
        </div>
      </header>

      <main>
        <Outlet />
      </main>

      <nav className="bottom-tabs" aria-label={t("nav.main")}>
        {[...tabs, ...extraTabs].map((tab) => (
          <button
            key={tab.to}
            type="button"
            className={selected === tab.to ? "active" : undefined}
            aria-current={selected === tab.to ? "page" : undefined}
            onClick={() => go(tab.to)}
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
