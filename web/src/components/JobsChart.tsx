// Jobs finished per hour over the last 24 hours (Status page): one series, so one colour
// and no legend; a tooltip per hour (also with the jobs that gave up) and a table for
// screen readers.
import { Caption1, Tooltip } from "@fluentui/react-components";

import type { QueueStats } from "../api";
import { useI18n } from "../i18n";

const hourLabel = (iso: string, locale: string) =>
  new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });

export function JobsChart({ hours }: { hours: QueueStats["per_hour"] }) {
  const { t, locale } = useI18n();
  const max = Math.max(1, ...hours.map((h) => h.done));
  const total = hours.reduce((n, h) => n + h.done, 0);
  return (
    <figure className="jobs-chart">
      <figcaption className="jobs-chart-caption">
        <Caption1 className="muted">{t("dashboard.jobsPerHour")}</Caption1>
        <Caption1 className="muted">{t("dashboard.jobsTotal", { n: total })}</Caption1>
      </figcaption>
      <div className="jobs-chart-plot" aria-hidden="true">
        <span className="jobs-chart-max">{max}</span>
        {hours.map((h) => (
          <Tooltip
            key={h.hour}
            relationship="description"
            content={
              <>
                {hourLabel(h.hour, locale)}: {t("dashboard.jobsDone", { n: h.done })}
                {h.failed > 0 && <> – {t("dashboard.jobsFailed", { n: h.failed })}</>}
              </>
            }
          >
            {/* The whole column is the hover target, not just the (maybe tiny) bar. */}
            <span className="jobs-chart-col" tabIndex={-1}>
              <span className="jobs-chart-bar" style={{ height: `${(h.done / max) * 100}%` }} />
            </span>
          </Tooltip>
        ))}
      </div>
      <div className="jobs-chart-axis" aria-hidden="true">
        {hours.map((h, i) => (
          <span key={h.hour}>{i % 6 === 0 ? hourLabel(h.hour, locale) : ""}</span>
        ))}
      </div>
      <table className="visually-hidden">
        <caption>{t("dashboard.jobsPerHour")}</caption>
        <tbody>
          {hours.map((h) => (
            <tr key={h.hour}>
              <th scope="row">{hourLabel(h.hour, locale)}</th>
              <td>{t("dashboard.jobsDone", { n: h.done })}</td>
              <td>{t("dashboard.jobsFailed", { n: h.failed })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
