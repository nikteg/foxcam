import {
  Button,
  Caption1,
  Menu,
  MenuDivider,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Tab,
  TabList,
  Text,
  Title2,
  Tooltip,
} from "@fluentui/react-components";
import {
  Archive20Regular,
  ArrowClockwise20Regular,
  Camera24Regular,
  DataBarVertical24Regular,
  Folder24Regular,
  Person20Regular,
  Person24Regular,
  ScanObject20Regular,
  Server24Regular,
  Settings24Regular,
  SignOut20Regular,
  Target24Regular,
  Wrench20Regular,
} from "@fluentui/react-icons";
import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState, type ReactElement } from "react";
import { Outlet, useLocation, useNavigate, useNavigationType } from "react-router";

import { api } from "./api";
import { useJobCounts, useNavBadges, type NavBadge } from "./navBadges";
import { useAuth, useAuthChanged, useUnauthorizedRedirect } from "./auth";
import { CountBadge, RouterLink, usePageTitle, useRouterHref } from "./components/ui";
import { AuthScreen } from "./pages/Login";
import { LanguageMenu, useI18n, type MessageKey } from "./i18n";
import { ThemeMenu } from "./theme";
import { NewVersionBanner } from "./components/NewVersionBanner";
import { AppToaster } from "./components/Toasts";
import { PageSpinner } from "./components/Skeletons";

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
/** Larger counts show as "999+": the button keeps room for that, so it never moves. */
const MAX_JOB_COUNT = 999;
/** A backlog this big is a warning; over 99, trouble. */
const JOB_WARN_AT = 10;
const JOB_DANGER_AT = 100;

const jobLevel = (n: number) =>
  n >= JOB_DANGER_AT ? " job-card-danger" : n >= JOB_WARN_AT ? " job-card-warn" : "";

const JOB_CARDS = [
  { kind: "preparation", label: "prep.tab", icon: <Wrench20Regular /> },
  { kind: "archiving", label: "compaction.title", icon: <Archive20Regular /> },
  { kind: "detection", label: "queue.queueTab", icon: <ScanObject20Regular /> },
] as const;

/** The jobs' backlog (admins): small buttons in the top bar (like a camera card's
 *  Recordings), always shown, in the order of the Jobs tabs; each opens its tab. */
function JobCounts() {
  const { t } = useI18n();
  const jobs = useJobCounts();
  const routerHref = useRouterHref();
  return (
    <span className="job-cards">
      {JOB_CARDS.map(({ kind, label, icon }) => {
        return (
          // The tooltip names it; the button's own text is the count.
          <Tooltip key={kind} content={t(label)} relationship="description">
            <Button
              as="a"
              size="small"
              icon={icon}
              className={`job-card${jobLevel(jobs[kind])}`}
              {...routerHref(`/jobs/${kind}`)}
            >
              <span className="job-count">
                {jobs[kind] > MAX_JOB_COUNT ? `${MAX_JOB_COUNT}+` : jobs[kind]}
              </span>
            </Button>
          </Tooltip>
        );
      })}
    </span>
  );
}

/** A nav icon with its number (new events) on the top right corner; none at 0. */
function NavIcon({ icon, badges }: { icon: ReactElement; badges: NavBadge[] }) {
  return (
    <span className="nav-icon">
      {icon}
      {badges.some((b) => b.count > 0) && (
        <span className="nav-badges">
          {badges.map((b) => (
            <CountBadge key={b.kind} {...b} />
          ))}
        </span>
      )}
    </span>
  );
}

/** Fetches everything on the page again (a home screen app has no pull to refresh). */
function RefreshButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const client = useQueryClient();
  // Spins until what is on screen has come back (not for background polling).
  const [busy, setBusy] = useState(false);
  return (
    <Button
      className={className}
      appearance="subtle"
      aria-label={t("common.refresh")}
      icon={<ArrowClockwise20Regular className={busy ? "spin" : undefined} />}
      onClick={() => {
        setBusy(true);
        void client.invalidateQueries().finally(() => setBusy(false));
      }}
    />
  );
}

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
  const auth = useAuth();
  useUnauthorizedRedirect();
  if (auth.isPending) return <PageSpinner />;
  // The last known state stays on screen when the check itself fails (e.g. offline).
  if (auth.error && !auth.data) {
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
  // drawers) do not count: opening a drawer after a reload turns the navigation type from
  // POP into PUSH on the same path, which must not scroll.
  const shownPath = useRef(pathname);
  useLayoutEffect(() => {
    if (shownPath.current === pathname) return;
    shownPath.current = pathname;
    if (navigationType !== "POP") window.scrollTo(0, 0);
  }, [pathname, navigationType]);
  const { t } = useI18n();
  const tabs = TABS.filter((tab) => isAdmin || !tab.admin);
  const extraTabs = EXTRA_TABS.filter((tab) => isAdmin || !tab.admin);
  const badges = useNavBadges(selected === "/events");
  const icon = (tab: NavItem) => <NavIcon icon={tab.icon} badges={badges[tab.to] ?? []} />;
  const topTab = (tab: NavItem) => (
    <Tab key={tab.to} value={tab.to} icon={icon(tab)}>
      {t(tab.label)}
    </Tab>
  );
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
            <img className="brand-icon" src="/icon.svg" alt="" width={336} height={344} />
            {/* Phones: the fox only (the job counts need the room). */}
            <span className="brand-name">Foxcam</span>
          </Text>
        </RouterLink>
        <TabList
          className="top-tabs"
          selectedValue={selected}
          onTabSelect={(_, d) => go(String(d.value))}
          size="medium"
        >
          {tabs.map(topTab)}
          {extraTabs.map(topTab)}
        </TabList>
        {/* Right-aligned header actions. */}
        <div className="topbar-actions">
          {/* Phones: no room (they are in Settings → General and on the Account page). With
              the bottom tab bar (tablets, phones, home screen app): no pull to refresh. */}
          {isAdmin && <JobCounts />}
          <span className="not-phone">
            <ThemeMenu />
            <LanguageMenu className="language-menu" />
          </span>
          <RefreshButton className="with-tabbar" />
          <UserMenu />
        </div>
      </header>

      <main>
        <NewVersionBanner />
        <Outlet />
      </main>
      <AppToaster />

      <nav className="bottom-tabs" aria-label={t("nav.main")}>
        {[...tabs, ...extraTabs].map((tab) => (
          <button
            key={tab.to}
            type="button"
            className={selected === tab.to ? "active" : undefined}
            aria-current={selected === tab.to ? "page" : undefined}
            onClick={() => go(tab.to)}
          >
            {icon(tab)}
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
