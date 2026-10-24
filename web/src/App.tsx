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
  Search24Regular,
  Server24Regular,
  Settings20Regular,
  Settings24Regular,
  Target24Regular,
  Video24Regular,
} from "@fluentui/react-icons";
import type { ReactElement } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";

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
  { to: "/live", label: "nav.live", short: "nav.live", icon: <Video24Regular /> },
  { to: "/events", label: "nav.events", short: "nav.eventsShort", icon: <Target24Regular /> },
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
];

function currentTab(pathname: string): string {
  if (pathname === "/") return "/";
  if (pathname.startsWith("/cameras")) return "/cameras";
  return TABS.find((t) => t.to !== "/" && pathname.startsWith(t.to))?.to ?? "";
}

/** Signed-in user: account page, settings (admins) and sign out. */
function UserMenu({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { data: auth } = useAuth();
  const changed = useAuthChanged();
  if (!auth?.user) {
    // Accounts turned off (auth.enabled: false): everyone is an admin, nobody signs in.
    return isAdmin ? (
      <Button
        appearance="subtle"
        icon={<Settings24Regular />}
        aria-label={t("nav.settings")}
        title={t("nav.settings")}
        onClick={() => void navigate("/settings")}
      />
    ) : null;
  }
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
          {isAdmin && (
            <MenuItem icon={<Settings20Regular />} onClick={() => void navigate("/settings")}>
              {t("nav.settings")}
            </MenuItem>
          )}
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
  const selected = currentTab(pathname);
  const { t } = useI18n();
  const tabs = TABS.filter((tab) => isAdmin || !tab.admin);

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
          {tabs.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
          {EXTRA_TABS.map((tab) => (
            <Tab key={tab.to} value={tab.to} icon={tab.icon}>
              {t(tab.label)}
            </Tab>
          ))}
        </TabList>
        {/* Right-aligned header actions; Settings is in the user menu. */}
        <div className="topbar-actions">
          <ThemeMenu />
          <LanguageMenu className="language-menu" />
          <UserMenu isAdmin={isAdmin} />
        </div>
      </header>

      <main>
        <Outlet />
      </main>

      <nav className="bottom-tabs" aria-label={t("nav.main")}>
        {[...tabs, ...EXTRA_TABS].map((tab) => (
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
