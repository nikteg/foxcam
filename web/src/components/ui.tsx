import {
  Button,
  Body1,
  Caption1,
  Card,
  Link,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  MessageBarTitle,
  ProgressBar,
  Spinner,
  Text,
  Title2,
  Tooltip,
  Tab,
  TabList,
} from "@fluentui/react-components";
import {
  ArrowRight20Regular,
  Checkmark16Filled,
  Dismiss20Regular,
  ChevronRight16Regular,
  WeatherMoon16Regular,
  WeatherMoon20Regular,
  WeatherSunny16Regular,
  WeatherSunny20Regular,
} from "@fluentui/react-icons";
import { useEffect, useState, type MouseEvent, type ReactElement, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";

import { localTime, percent, relativeTime } from "../format";
import { useI18n } from "../i18n";

/** Fluent link that navigates client-side (keeps cmd/ctrl-click working). */
export function RouterLink({
  to,
  className,
  title,
  children,
}: {
  to: string;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <Link
      href={to}
      className={className}
      title={title}
      onClick={(e) => followLink(e, () => navigate(to))}
    >
      {children}
    </Link>
  );
}

/** Props for a Fluent `<Button as="a">` that navigates inside the app. */
export function useRouterHref() {
  const navigate = useNavigate();
  return (to: string) => ({
    href: to,
    onClick: (e: MouseEvent<HTMLAnchorElement>) => followLink(e, () => navigate(to)),
  });
}

function followLink(e: MouseEvent, go: () => void) {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  go();
}

const APP_NAME = "Media Streamer";

/** Sets the browser tab title, so history and bookmarks name the page. */
export function usePageTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} – ${APP_NAME}` : APP_NAME;
  }, [title]);
}

/** The page's name for the browser tab and screen readers. Not shown: the nav bar and
 * the page's tabs already say where you are. */
export function PageTitle({ title }: { title: string }) {
  usePageTitle(title);
  return (
    <Title2 as="h1" className="visually-hidden">
      {title}
    </Title2>
  );
}

const DISMISSED = "media-streamer.banners-dismissed";

/** An explanation at the top of a page or tab, until the user closes it (remembered in
 * this browser). ``id`` names it for that. */
export function InfoBanner({ id, children }: { id: string; children: ReactNode }) {
  const { t } = useI18n();
  const [hidden, setHidden] = useState(() => {
    try {
      return (JSON.parse(localStorage.getItem(DISMISSED) ?? "[]") as string[]).includes(id);
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  const dismiss = () => {
    try {
      const ids = JSON.parse(localStorage.getItem(DISMISSED) ?? "[]") as string[];
      localStorage.setItem(DISMISSED, JSON.stringify([...new Set([...ids, id])]));
    } catch {
      // private browsing: hide it for now only
    }
    setHidden(true);
  };
  return (
    <MessageBar intent="info" className="block info-banner" layout="multiline">
      <MessageBarBody>{children}</MessageBarBody>
      <MessageBarActions
        containerAction={
          <Button
            appearance="transparent"
            icon={<Dismiss20Regular />}
            aria-label={t("common.close")}
            onClick={dismiss}
          />
        }
      />
    </MessageBar>
  );
}

/** A section heading's link to its full page (Status: all cameras, events, jobs): a
 * small arrow next to the heading, named by its tooltip. */
export function SectionLink({ to, label }: { to: string; label: string }) {
  const routerHref = useRouterHref();
  return (
    <Tooltip content={label} relationship="label">
      <Button
        as="a"
        size="small"
        appearance="subtle"
        className="section-link"
        icon={<ArrowRight20Regular />}
        {...routerHref(to)}
      />
    </Tooltip>
  );
}

/** The actions of a page or tab (Add …), first thing on it, at normal size. */
export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function PageHeader({
  title,
  children,
  actions,
}: {
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  usePageTitle(title);
  return (
    <div className="page-header">
      <div className="page-title">
        <Title2 as="h1">{title}</Title2>
        {actions}
      </div>
      {children && <Body1 className="muted">{children}</Body1>}
    </div>
  );
}

export function Time({
  iso,
  relative = false,
}: {
  iso: string | null | undefined;
  relative?: boolean;
}) {
  if (!iso) return <span className="muted">–</span>;
  return (
    <Tooltip content={iso} relationship="description">
      <time dateTime={iso}>{relative ? relativeTime(iso) : localTime(iso)}</time>
    </Tooltip>
  );
}

/** Online/offline dot. A plain element with fixed geometry, so switching state never moves
 * the text next to it. */
export function Dot({ ok }: { ok: boolean }) {
  const { t } = useI18n();
  const label = ok ? t("common.online") : t("common.offline");
  return (
    <span className={`status-dot ${ok ? "ok" : "bad"}`} title={label}>
      <span className="visually-hidden">{label}</span>
    </span>
  );
}

/** Tone of a tag per state / kind: a coloured dot (or a brand tint for target labels). */
const TAG_TONES: Record<string, string> = {
  done: "success",
  ok: "success",
  failed: "danger",
  dead: "danger",
  bad: "danger",
  leased: "warning",
  queued: "warning",
  warn: "warning",
  pending: "info",
  new: "info",
  private: "important",
  target: "brand",
};

/** Translated name of a job / segment state ("done", "pending", ...). */
export function useStateLabel() {
  const { t } = useI18n();
  return (state: string) => {
    switch (state) {
      case "new":
        return t("state.new");
      case "queued":
        return t("state.queued");
      case "done":
        return t("state.done");
      case "private":
        return t("state.private");
      case "failed":
        return t("state.failed");
      case "pending":
        return t("state.pending");
      case "leased":
        return t("state.leased");
      case "dead":
        return t("state.dead");
      case "skipped":
        return t("state.skipped");
      default:
        return state;
    }
  };
}

/** Translated camera source type ("tapo_cloud" -> "Tapo cloud"). */
export function useSourceLabel() {
  const { t } = useI18n();
  return (type: string) => {
    switch (type) {
      case "tapo_cloud":
        return t("source.tapo_cloud");
      case "rtsp":
        return t("source.rtsp");
      case "file":
        return t("source.file");
      case "test":
        return t("source.test");
      default:
        return type;
    }
  };
}

/** Status pill; `kind` is a job/segment state or ok/warn/bad. */
export function Tag({
  kind,
  title,
  icon,
  children,
}: {
  kind?: string;
  title?: string;
  icon?: ReactElement;
  children?: ReactNode;
}) {
  // One neutral pill in both themes (Fluent's tinted badges are faint in the light theme);
  // the state is a coloured dot next to readable text.
  const tone = (kind && TAG_TONES[kind]) || "neutral";
  const badge = (
    <span className={`app-tag tone-${tone}`}>
      {icon ?? (tone !== "neutral" && tone !== "brand" && <span className="app-tag-dot" />)}
      {children}
    </span>
  );
  return title ? (
    <Tooltip content={title} relationship="description">
      {badge}
    </Tooltip>
  ) : (
    badge
  );
}

export function Light({
  night,
  long = false,
  small = false,
}: {
  night: boolean | null;
  long?: boolean;
  /** 16 px icon, for use inside a Tag (a 20 px icon makes the tag taller). */
  small?: boolean;
}) {
  const { t } = useI18n();
  if (night === null) return <span className="muted">–</span>;
  const label = night ? t("light.night") : t("light.day");
  const Icon = small
    ? night
      ? WeatherMoon16Regular
      : WeatherSunny16Regular
    : night
      ? WeatherMoon20Regular
      : WeatherSunny20Regular;
  return (
    <span className="light" title={night ? t("light.nightIr") : t("light.day")}>
      <Icon />
      {long && <span>{label}</span>}
    </span>
  );
}

/** A labelled number (Status page, Jobs counters): icon, label, value and a hint. With
 * `onClick` it is a toggle (e.g. a filter), `selected` when on. */
export function Stat({
  label,
  value,
  icon,
  hint,
  selected,
  onClick,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactElement;
  hint?: ReactNode;
  selected?: boolean;
  onClick?: () => void;
}) {
  const classes = ["stat", onClick && "clickable", selected && "selected"];
  return (
    <Card
      className={classes.filter(Boolean).join(" ")}
      appearance="outline"
      size="small"
      onClick={onClick}
      aria-pressed={onClick ? !!selected : undefined}
    >
      <div className="stat-head">
        {icon && (
          <span className="stat-icon" aria-hidden>
            {icon}
          </span>
        )}
        <Caption1 className="stat-label">{label}</Caption1>
        {/* Pressable: a chevron, a checkmark while on (also visible on touch screens). */}
        {onClick && (
          <span className="stat-toggle" aria-hidden>
            {selected ? <Checkmark16Filled /> : <ChevronRight16Regular />}
          </span>
        )}
      </div>
      <Text className="stat-value">{value}</Text>
      {hint && <Caption1 className="muted stat-hint">{hint}</Caption1>}
    </Card>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  return (
    <span className="conf-cell">
      <ProgressBar
        className="conf-bar"
        value={value}
        thickness="large"
        color={value >= 0.5 ? "brand" : "warning"}
      />
      <Text size={200}>{percent(value, 1)}</Text>
    </span>
  );
}

export function QueryState({ error, loading }: { error: unknown; loading: boolean }) {
  const { t } = useI18n();
  if (error) {
    return (
      <MessageBar intent="error" className="block">
        <MessageBarBody>
          <MessageBarTitle>{t("common.loadFailed")}</MessageBarTitle>
          {String((error as Error).message ?? error)}
        </MessageBarBody>
      </MessageBar>
    );
  }
  if (loading) return <Spinner size="small" label={t("common.loading")} className="block" />;
  return null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <Body1 className="muted empty">{children}</Body1>;
}

/** Bottom row of a form or dialog step: going back on the left, the step's actions on the
 *  right. Wraps on narrow screens (the actions stay right-aligned). */
export function ActionRow({ back, children }: { back?: ReactNode; children?: ReactNode }) {
  return (
    <div className="action-row">
      {back && <div className="action-row-back">{back}</div>}
      <div className="action-row-end">{children}</div>
    </div>
  );
}

/** The sub-page of a tabbed page, from the URL: /settings/users -> "users". The base path
 * (/settings) is the first tab. */
export function useSubPage<T extends string>(base: string, ids: readonly T[]): T {
  const { pathname } = useLocation();
  const rest = pathname.slice(base.length).replace(/^\//, "").split("/")[0];
  return ids.find((id) => id === rest) ?? ids[0]!;
}

/** Tabs that are sub-pages with their own URL (links and the back button work, and
 * nothing depends on scrolling to an anchor). The first tab is the base path. */
export function PageTabs<T extends string>({
  base,
  current,
  tabs,
}: {
  base: string;
  current: T;
  tabs: { id: T; label: string }[];
}) {
  const navigate = useNavigate();
  return (
    <TabList
      className="block page-tabs"
      selectedValue={current}
      onTabSelect={(_, d) =>
        void navigate(d.value === tabs[0]?.id ? base : `${base}/${String(d.value)}`)
      }
    >
      {tabs.map((tab) => (
        <Tab key={tab.id} value={tab.id}>
          {tab.label}
        </Tab>
      ))}
    </TabList>
  );
}
