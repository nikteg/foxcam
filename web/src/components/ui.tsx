import {
  Button,
  Body1,
  Caption1,
  Card,
  Checkbox as FluentCheckbox,
  Input,
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
  type ButtonProps,
  type CheckboxProps,
} from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  ArrowRight20Regular,
  LockClosed16Regular,
  Checkmark16Filled,
  Dismiss20Regular,
  ChevronRight16Regular,
  WeatherMoon16Regular,
  WeatherMoon20Regular,
  WeatherSunny16Regular,
  WeatherSunny20Regular,
} from "@fluentui/react-icons";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type HTMLAttributes,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router";

import { localTime, percent, relativeTime } from "../format";
import { useI18n } from "../i18n";

/** Running as an iOS home screen app (no browser UI around it). */
const standalone =
  (navigator as { standalone?: boolean }).standalone === true ||
  window.matchMedia("(display-mode: standalone)").matches;

/** The file name in a Content-Disposition header, else the URL's last part. */
function fileName(res: Response, href: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd);
  if (m) return decodeURIComponent(m[1] ?? m[2]!);
  return new URL(href, location.href).pathname.split("/").pop() || "download";
}

async function fetchFile(href: string): Promise<File> {
  const res = await fetch(href, { credentials: "same-origin" });
  if (!res.ok) throw new Error(String(res.status));
  const blob = await res.blob();
  return new File([blob], fileName(res, href), { type: blob.type });
}

/** Hand a file to the share sheet (Save Video / Save to Files), or download it from memory
 * where there is no share sheet for files. "retry": iOS wants a fresh tap for the sheet
 * (the fetch took too long after the first one). */
async function saveFile(file: File): Promise<"done" | "retry"> {
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file] });
    } catch (e) {
      const name = (e as Error).name;
      if (name === "NotAllowedError") return "retry";
      if (name !== "AbortError") throw e; // AbortError: closed the sheet, fine
    }
    return "done";
  }
  const url = URL.createObjectURL(file);
  const a = Object.assign(document.createElement("a"), { href: url, download: file.name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return "done";
}

/** A download link styled as a button. In a home screen app iOS opens a file in a
 * full-screen viewer with no way back, so there:
 * - ``small`` files (clips, exports: a few MB) are fetched into memory and handed to the
 *   share sheet (Save Video, Save to Files);
 * - large ones (recordings, up to GBs: too big for memory) open in a new window, a sheet
 *   with Done. */
export function DownloadButton({
  href,
  appearance,
  small = false,
  children,
}: {
  href: string;
  appearance?: ButtonProps["appearance"];
  small?: boolean;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  // Fetched, waiting for a second tap to open the share sheet.
  const [ready, setReady] = useState<File | null>(null);
  const save = async () => {
    const file = ready ?? (await fetchFile(href));
    setReady((await saveFile(file)) === "retry" ? file : null);
  };
  return (
    <Button
      as="a"
      href={href}
      download
      appearance={ready ? "primary" : appearance}
      disabled={busy}
      icon={busy ? <Spinner size="extra-tiny" /> : <ArrowDownload20Regular />}
      {...(standalone ? { target: "_blank", rel: "noopener" } : {})}
      onClick={
        standalone && small
          ? (e: MouseEvent) => {
              e.preventDefault();
              setBusy(true);
              // Failed (e.g. a clip in S3 without CORS): the new window as before.
              save()
                .catch(() => window.open(href, "_blank", "noopener"))
                .finally(() => setBusy(false));
            }
          : undefined
      }
    >
      {ready ? t("common.saveFile") : children}
    </Button>
  );
}

/** Fluent Checkbox with an id of its own. Inside a <Field> it takes the field's id, so with
 * several in one Field (a group of options) every label pointed at the first box and
 * pressing a label did not toggle its own checkbox. */
export function Checkbox(props: CheckboxProps) {
  const id = useId();
  return <FluentCheckbox id={id} {...props} />;
}

/** Date or time input with a hint while empty. iOS Safari shows an empty date/time input
 * as a blank box and ignores placeholder, so the hint is drawn over it there (styles.css). */
export function DateTimeInput({
  type,
  value,
  placeholder,
  onChange,
  ...rest
}: {
  type: "date" | "time";
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  "aria-label"?: string;
}) {
  return (
    <Input
      {...rest}
      type={type}
      value={value}
      placeholder={placeholder}
      className="date-time-input"
      root={
        { "data-placeholder": value ? undefined : placeholder } as HTMLAttributes<HTMLSpanElement>
      }
      onChange={(_, d) => onChange(d.value)}
    />
  );
}

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

const APP_NAME = "Foxcam";

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

const DISMISSED = "foxcam.banners-dismissed";

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
  config: "config",
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
      {icon ?? (!["neutral", "brand", "config"].includes(tone) && <span className="app-tag-dot" />)}
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

/** Label / value rows (cards, drawers): <Facts><Fact label="Start">…</Fact></Facts>. */
export function Facts({ className, children }: { className?: string; children: ReactNode }) {
  return <dl className={`facts ${className ?? ""}`}>{children}</dl>;
}

export function Fact({
  label,
  className,
  labelClassName,
  children,
}: {
  label: ReactNode;
  /** On the value, e.g. "mono" or "row wrap". */
  className?: string;
  labelClassName?: string;
  children?: ReactNode;
}) {
  return (
    <>
      <dt className={labelClassName}>{label}</dt>
      <dd className={className}>{children}</dd>
    </>
  );
}

/** "Defined in config.yaml": read-only here. Stands apart from state tags. */
export function ConfigTag() {
  const { t } = useI18n();
  return (
    <Tag kind="config" title={t("common.configTagHint")} icon={<LockClosed16Regular />}>
      config.yaml
    </Tag>
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
  // Scrolls sideways on narrow screens: keep the open tab in view.
  const list = useRef<HTMLDivElement>(null);
  // Only the bar scrolls (scrollIntoView would move the page too, also vertically).
  useEffect(() => {
    const bar = list.current;
    const tab = bar?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!bar || !tab) return;
    const box = bar.getBoundingClientRect();
    const r = tab.getBoundingClientRect();
    const left = r.left - box.left + bar.scrollLeft;
    const right = r.right - box.left + bar.scrollLeft;
    if (left < bar.scrollLeft) bar.scrollTo({ left, behavior: "smooth" });
    else if (right > bar.scrollLeft + bar.clientWidth)
      bar.scrollTo({ left: right - bar.clientWidth, behavior: "smooth" });
  }, [current]);
  return (
    <TabList
      ref={list}
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
