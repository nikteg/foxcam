// Locale-aware formatting. The locale is set by <I18nProvider>, which remounts the app
// when it changes, so these plain functions always render with the current language.

let locale = "en-GB";
const cache = new Map<string, Intl.DateTimeFormat | Intl.RelativeTimeFormat | Intl.NumberFormat>();

export function setFormatLocale(next: string) {
  locale = next;
}

function cached<T extends Intl.DateTimeFormat | Intl.RelativeTimeFormat | Intl.NumberFormat>(
  key: string,
  make: () => T,
): T {
  const k = `${locale}|${key}`;
  let f = cache.get(k) as T | undefined;
  if (!f) {
    f = make();
    cache.set(k, f);
  }
  return f;
}

const number = (digits: number) =>
  cached(
    `n${digits}`,
    () =>
      new Intl.NumberFormat(locale, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }),
  );

export function fileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit++;
  }
  return `${number(unit === 0 ? 0 : 1).format(n)} ${units[unit]}`;
}

/** Compact duration: "45s", "3m 20s", "2h 5m" (Swedish: "3 min 20 s"). */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  const sv = locale.startsWith("sv");
  const [h, m, s] = sv ? [" h", " min", " s"] : ["h", "m", "s"];
  const total = Math.round(seconds);
  if (total < 60) return `${total}${s}`;
  if (total < 3600) return `${Math.floor(total / 60)}${m} ${total % 60}${s}`;
  return `${Math.floor(total / 3600)}${h} ${Math.floor((total % 3600) / 60)}${m}`;
}

/** 0.838 -> "84%" / "84 %" (digits = decimals). */
export const percent = (value: number, digits = 0) =>
  cached(
    `p${digits}`,
    () =>
      new Intl.NumberFormat(locale, {
        style: "percent",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }),
  ).format(value);

export const decimal = (value: number, digits = 1) => number(digits).format(value);

export function localTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return cached(
    "dt",
    () =>
      new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
  ).format(d);
}

/** "Friday 24 October" (local time): headings for lists grouped by day. */
export function localDay(iso: string): string {
  return cached(
    "day",
    () => new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long" }),
  ).format(new Date(iso));
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "–";
  const rtf = cached("rel", () => new Intl.RelativeTimeFormat(locale, { numeric: "auto" }));
  const s = Math.round((new Date(iso).getTime() - now) / 1000);
  if (Math.abs(s) < 60) return rtf.format(s, "second");
  if (Math.abs(s) < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (Math.abs(s) < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}
