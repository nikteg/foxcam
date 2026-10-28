// A camera's sun limit: targets seen while the sun is lower are kept but are no match (a
// window reflecting the lit room after dark). The chart shows where the camera's
// detections sit on the sun's curve, so the limit can go just above the wrong ones.
import {
  Button,
  Caption1,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Switch,
} from "@fluentui/react-components";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Camera, type SunDetection } from "../api";
import { useI18n } from "../i18n";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";
import { HEIGHT as CHART_HEIGHT, kindOf, snap, SunChart, type Kind } from "./SunChart";
import { DrawerTitle, Fact, Facts, QueryState } from "./ui";
import { BoxSkeleton, FieldSkeleton } from "./Skeletons";

const DAYS = [7, 14, 30, 90];
const DEFAULT_DAYS = 14;
/** Where the limit starts when switched on: the end of civil twilight is too late for a
 * lit room, sunset is a fair first guess. */
const FIRST_LIMIT = 0;

/** The chart's data, for prefetching where the drawer is opened from (the request takes a
 * while, and a chart popping in while the drawer slides in looks laggy). */
export const sunChartQuery = (cameraId: string, days = DEFAULT_DAYS) => ({
  queryKey: ["sun", cameraId, days],
  queryFn: () => api.sunChart(cameraId, days),
  staleTime: 60_000,
});

const parse = (text: string) => (text.trim() === "" ? null : Number(text));

export function SunDrawer({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [days, setDays] = useState(DEFAULT_DAYS);
  const [limit, setLimit] = useState(camera.sun_limit);
  const [last, setLast] = useState(camera.sun_limit ?? FIRST_LIMIT);
  const [lat, setLat] = useState(camera.latitude?.toString() ?? "");
  const [lon, setLon] = useState(camera.longitude?.toString() ?? "");
  const chart = useQuery({
    ...sunChartQuery(camera.id, days),
    placeholderData: (prev) => prev,
  });
  const save = useMutation({
    mutationFn: () => api.setSun(camera.id, { limit, latitude: parse(lat), longitude: parse(lon) }),
    onSuccess: () => {
      for (const key of ["cameras", "sun", "events", "detections", "segments"])
        void client.invalidateQueries({ queryKey: [key] });
      onClose();
    },
  });
  const setOn = (on: boolean) => setLimit(on ? last : null);
  const move = (value: number) => {
    setLimit(value);
    setLast(value);
  };
  const badPlace = (lat === "") !== (lon === "") || [lat, lon].some((v) => Number.isNaN(parse(v)));

  return (
    <Dialog open size="large" onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>
            <DrawerTitle title={t("sun.title")} sub={camera.name} />
          </DialogTitle>
          <DialogContent className="form-stack">
            <Caption1 className="muted">{t("sun.help")}</Caption1>
            <Switch
              checked={limit !== null}
              onChange={(_, d) => setOn(d.checked)}
              label={t("sun.enabled")}
            />
            <QueryState
              error={chart.error}
              loading={chart.isPending}
              skeleton={
                <>
                  <FieldSkeleton />
                  <BoxSkeleton height={CHART_HEIGHT} />
                </>
              }
            />
            {chart.data && !chart.data.located && (
              <MessageBar intent="warning">
                <MessageBarBody>{t("sun.noLocation")}</MessageBarBody>
              </MessageBar>
            )}
            {chart.data?.located && (
              <>
                <div className="row wrap sun-controls">
                  <Field label={t("sun.limit")} hint={t("sun.limitHint")}>
                    <Input
                      type="number"
                      step={0.5}
                      disabled={limit === null}
                      value={(limit ?? last).toString()}
                      contentAfter="°"
                      onChange={(_, d) => {
                        const v = parse(d.value);
                        if (v !== null && !Number.isNaN(v)) move(snap(v));
                      }}
                    />
                  </Field>
                  <Field label={t("sun.period")}>
                    <Select value={String(days)} onChange={(_, d) => setDays(Number(d.value))}>
                      {DAYS.map((n) => (
                        <option key={n} value={n}>
                          {t("sun.days", { n })}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <SunChart data={chart.data} limit={limit} onLimit={move} />
                <Legend detections={chart.data.detections} limit={limit} />
              </>
            )}
            <div className="row wrap sun-controls">
              <Field label={t("sun.latitude")}>
                <Input type="number" step="any" value={lat} onChange={(_, d) => setLat(d.value)} />
              </Field>
              <Field label={t("sun.longitude")}>
                <Input type="number" step="any" value={lon} onChange={(_, d) => setLon(d.value)} />
              </Field>
            </div>
            <Caption1 className="muted">{t("sun.locationHint")}</Caption1>
            {save.error && (
              <MessageBar intent="error">
                <MessageBarBody>{save.error.message}</MessageBarBody>
              </MessageBar>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button
              appearance="primary"
              disabled={save.isPending || badPlace}
              onClick={() => save.mutate()}
            >
              {t("common.save")}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

/** What each mark is, and how many of each the limit leaves out and keeps. */
function Legend({ detections, limit }: { detections: SunDetection[]; limit: number | null }) {
  const { t } = useI18n();
  const kinds: Kind[] = ["wrong", "correct", "unmarked"];
  const count = (kind: Kind, ignored: boolean) =>
    detections.filter(
      (d) => kindOf(d) === kind && (limit !== null && d.elevation < limit) === ignored,
    ).length;
  return (
    <Facts className="sun-legend">
      {kinds.map((kind) => (
        <Fact
          key={kind}
          label={
            <span className="row">
              <svg width="12" height="12" aria-hidden className={`sun-key ${kind}`}>
                {kind === "wrong" ? (
                  <rect x="2.5" y="2.5" width="7" height="7" transform="rotate(45 6 6)" />
                ) : (
                  <circle cx="6" cy="6" r="4" />
                )}
              </svg>
              {t(`sun.kind.${kind}`)}
            </span>
          }
        >
          {t("sun.counts", { ignored: count(kind, true), kept: count(kind, false) })}
        </Fact>
      ))}
      <Fact
        label={
          <span className="row">
            <svg width="12" height="12" aria-hidden className="sun-key">
              <rect className="sun-band" x="0" y="2" width="12" height="8" />
              <line className="sun-curve" x1="1" x2="11" y1="6" y2="6" />
            </svg>
            {t("sun.curve")}
          </span>
        }
      >
        {t("sun.curveKey")}
      </Fact>
    </Facts>
  );
}
