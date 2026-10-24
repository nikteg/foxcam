import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  CardPreview,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Text,
} from "@fluentui/react-components";
import { ArrowDownload20Regular, Dismiss24Regular, Play20Filled } from "@fluentui/react-icons";
import { useState } from "react";

import type { FoxEvent } from "../api";
import { duration, localTime, percent } from "../format";
import { useI18n } from "../i18n";
import { Empty, Light, Tag, Time } from "./ui";

export function EventsGrid({ events }: { events: FoxEvent[] }) {
  const { t } = useI18n();
  const [playing, setPlaying] = useState<FoxEvent | null>(null);
  if (events.length === 0) return <Empty>{t("events.none")}</Empty>;

  return (
    <>
      <div className="cards">
        {events.map((e) => (
          <Card key={e.id} className="event-card">
            <CardPreview>
              {e.thumb_url ? (
                <button
                  type="button"
                  className="thumb-button"
                  disabled={!e.clip_url}
                  onClick={() => setPlaying(e)}
                  aria-label={t("events.playClip", { label: e.label })}
                >
                  <img
                    className="thumb"
                    loading="lazy"
                    src={e.thumb_url}
                    alt={t("events.thumbAlt", { label: e.label, camera: e.camera_id })}
                  />
                  {e.clip_url && (
                    <span className="play-overlay" aria-hidden="true">
                      <Play20Filled />
                    </span>
                  )}
                </button>
              ) : (
                <div className="thumb placeholder">
                  {e.building ? t("events.buildingClip") : t("events.noThumbnail")}
                </div>
              )}
            </CardPreview>
            <CardHeader
              header={
                <span className="row">
                  <Text weight="semibold">{e.label}</Text>
                  <Text weight="bold" className="brand-text">
                    {percent(e.max_confidence)}
                  </Text>
                  <Light night={e.is_night} />
                  <Caption1 className="muted">{e.camera_id}</Caption1>
                </span>
              }
              description={
                <Caption1 className="muted">
                  <Time iso={e.first_ts} /> ·{" "}
                  {t("events.detectionCount", { count: e.detection_count })} ·{" "}
                  {duration(e.clip_duration_s)}
                </Caption1>
              }
            />
            <CardFooter>
              {e.clip_url ? (
                <>
                  <Button
                    appearance="primary"
                    icon={<Play20Filled />}
                    onClick={() => setPlaying(e)}
                  >
                    {t("common.play")}
                  </Button>
                  <Button
                    as="a"
                    href={`${e.clip_url}?download=true`}
                    icon={<ArrowDownload20Regular />}
                  >
                    {t("common.download")}
                  </Button>
                </>
              ) : e.clip_error ? (
                <Tag kind="bad" title={e.clip_error}>
                  {t("events.clipFailed")}
                </Tag>
              ) : (
                <Tag kind="pending">{t("events.buildingClip")}</Tag>
              )}
            </CardFooter>
          </Card>
        ))}
      </div>

      <Dialog open={!!playing} onOpenChange={(_, d) => !d.open && setPlaying(null)}>
        <DialogSurface className="clip-dialog">
          {playing && (
            <DialogBody>
              <DialogTitle
                action={
                  <Button
                    appearance="subtle"
                    aria-label={t("common.close")}
                    icon={<Dismiss24Regular />}
                    onClick={() => setPlaying(null)}
                  />
                }
              >
                {playing.label} · {playing.camera_id} ·{" "}
                <span className="nowrap">{localTime(playing.first_ts)}</span>
              </DialogTitle>
              <DialogContent>
                <video
                  className="detail-media"
                  src={playing.clip_url ?? undefined}
                  poster={playing.thumb_url ?? undefined}
                  controls
                  autoPlay
                  playsInline
                />
              </DialogContent>
              <DialogActions>
                <Button
                  as="a"
                  href={`${playing.clip_url}?download=true`}
                  icon={<ArrowDownload20Regular />}
                >
                  {t("common.download")}
                </Button>
                <Button appearance="primary" onClick={() => setPlaying(null)}>
                  {t("common.close")}
                </Button>
              </DialogActions>
            </DialogBody>
          )}
        </DialogSurface>
      </Dialog>
    </>
  );
}
