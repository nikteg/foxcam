import { Button, Caption1, Input, Select } from "@fluentui/react-components";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type LogEntry, type LogProcess } from "../api";
import { fileSize, localTime, relativeTime } from "../format";
import { useI18n, type I18n } from "../i18n";
import { useFilters } from "../useFilters";
import { DownloadButton, Empty, InfoBanner, QueryState, Toolbar } from "./ui";
import { TextSkeleton } from "./Skeletons";

const LEVELS = ["", "INFO", "WARNING", "ERROR"] as const;

/** Settings → Logs: the newest lines of a process's log file, filtered by level and text. */
export function LogsSection() {
  const { t } = useI18n();
  const [f, setFilter] = useFilters(["process", "level", "q"] as const);
  const files = useQuery({
    queryKey: ["log-files"],
    queryFn: api.logFiles,
    refetchInterval: 30_000,
  });
  const available = files.data ?? [];
  const process = (available.find((l) => l.process === f.process) ?? available[0])?.process;
  const level = LEVELS.find((l) => l === f.level) ?? "";
  const entries = useQuery({
    queryKey: ["logs", process, level, f.q],
    queryFn: () =>
      api.logs(process as LogProcess, { level: level || undefined, q: f.q || undefined }),
    enabled: !!process,
    // A new filter keeps the current lines on screen until its own arrive.
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  const file = available.find((l) => l.process === process);

  return (
    <>
      {process && (
        <Toolbar>
          <DownloadButton href={`/api/logs/${process}/download`}>
            {t("logs.download")}
          </DownloadButton>
        </Toolbar>
      )}
      <InfoBanner id="logs">{t("logs.intro")}</InfoBanner>
      <QueryState error={files.error} loading={files.isPending} skeleton={<TextSkeleton />} />
      {files.data && available.length === 0 && <Empty>{t("logs.noFiles")}</Empty>}
      {process && (
        <>
          <div className="filters">
            <Select
              value={process}
              aria-label={t("logs.process")}
              onChange={(_, d) => setFilter("process", d.value)}
            >
              {available.map((l) => (
                <option key={l.process} value={l.process}>
                  {processName(t, l.process)}
                </option>
              ))}
            </Select>
            <Select
              value={level}
              aria-label={t("logs.level")}
              onChange={(_, d) => setFilter("level", d.value)}
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {t(`logs.level.${l || "all"}`)}
                </option>
              ))}
            </Select>
            <Input
              key={f.q}
              placeholder={t("logs.search")}
              aria-label={t("logs.search")}
              defaultValue={f.q}
              onKeyDown={(e) => e.key === "Enter" && setFilter("q", e.currentTarget.value.trim())}
              onBlur={(e) => setFilter("q", e.currentTarget.value.trim())}
            />
          </div>
          {file && (
            <div className="block">
              <Caption1 className="muted">
                {t("logs.fileInfo", {
                  size: fileSize(file.bytes),
                  updated: relativeTime(file.updated_at),
                })}
              </Caption1>
            </div>
          )}
          <QueryState
            error={entries.error}
            loading={entries.isPending}
            skeleton={<TextSkeleton />}
          />
          {entries.data?.length === 0 && <Empty>{t("logs.none")}</Empty>}
          {!!entries.data?.length && <LogLines entries={entries.data} />}
        </>
      )}
    </>
  );
}

function processName(t: I18n["t"], process: LogProcess): string {
  if (process.startsWith("worker-")) {
    return t("logs.process.worker", { id: process.slice("worker-".length) });
  }
  const known = ["server", "ingest", "compact", "mediamtx"] as const;
  const name = known.find((k) => k === process);
  return name ? t(`logs.process.${name}`) : process;
}

/** Log entries as text, newest first: time (local), level, logger, message. */
function LogLines({ entries }: { entries: LogEntry[] }) {
  // Entries have no id: time, logger and text, numbered when the same line repeats, so
  // keys stay put when newer lines come in on top.
  const seen = new Map<string, number>();
  const keyed = entries.map((e) => {
    const base = `${e.ts} ${e.logger} ${e.message}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return { e, key: `${base} ${n}` };
  });
  return (
    <div className="log-lines" role="log">
      {keyed.map(({ e, key }) => (
        <LogLine key={key} entry={e} />
      ))}
    </div>
  );
}

/** One entry; a long one (a traceback) shows its first and last line (the error) until
 *  opened. */
function LogLine({ entry: e }: { entry: LogEntry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const lines = e.message.split("\n");
  const hidden = lines.length - 2;
  const folded = hidden > 0 ? `${lines[0]}\n${lines.at(-1)}` : e.message;
  return (
    <div className={`log-line log-${e.level.toLowerCase()}`}>
      <span className="log-time">{localTime(e.ts)}</span>{" "}
      <span className="log-level">{e.level}</span> <span className="log-logger">{e.logger}</span>{" "}
      <span className="log-message">{open ? e.message : folded}</span>
      {hidden > 0 && (
        <div>
          <Button size="small" appearance="subtle" onClick={() => setOpen(!open)}>
            {open ? t("logs.fewerLines") : t("logs.moreLines", { n: hidden })}
          </Button>
        </div>
      )}
    </div>
  );
}
