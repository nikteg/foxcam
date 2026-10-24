import {
  Badge,
  Body1,
  Caption1,
  Card,
  Link,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  ProgressBar,
  Spinner,
  Text,
  Title2,
  Tooltip,
  type BadgeProps,
} from "@fluentui/react-components";
import {
  WeatherMoon16Regular,
  WeatherMoon20Regular,
  WeatherSunny16Regular,
  WeatherSunny20Regular,
} from "@fluentui/react-icons";
import { useEffect, type MouseEvent, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { localTime, percent, relativeTime } from "../format";
import { useI18n } from "../i18n";

/** Fluent link that navigates client-side (keeps cmd/ctrl-click working). */
export function RouterLink({
  to,
  className,
  children,
}: {
  to: string;
  className?: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <Link href={to} className={className} onClick={(e) => followLink(e, () => navigate(to))}>
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

const STATE_COLORS: Record<string, BadgeProps["color"]> = {
  done: "success",
  ok: "success",
  failed: "danger",
  dead: "danger",
  bad: "danger",
  leased: "warning",
  queued: "warning",
  warn: "warning",
  pending: "informative",
  new: "informative",
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
  icon?: BadgeProps["icon"];
  children?: ReactNode;
}) {
  const badge = (
    <Badge appearance="tint" color={(kind && STATE_COLORS[kind]) || "subtle"} icon={icon}>
      {children}
    </Badge>
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

export function Stat({
  label,
  value,
  selected,
  onClick,
}: {
  label: string;
  value: ReactNode;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <Card
      className={`stat ${selected ? "selected" : ""}`}
      appearance="filled-alternative"
      size="small"
      onClick={onClick}
    >
      <Caption1 className="muted stat-label">{label}</Caption1>
      <Text size={600} weight="semibold">
        {value}
      </Text>
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
