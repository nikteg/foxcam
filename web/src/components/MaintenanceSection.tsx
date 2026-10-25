// Settings → Detection: run the detection rules again on what is already there.
import {
  Button,
  Divider,
  Field,
  MessageBar,
  MessageBarBody,
  Subtitle2,
} from "@fluentui/react-components";
import { ArrowClockwise20Regular, ArrowSync20Regular } from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useI18n } from "../i18n";
import { CameraSelect } from "./CameraSelect";
import { ConfirmPopover } from "./ConfirmPopover";
import { DateTimeInput } from "./ui";

const today = () => new Date().toLocaleDateString("sv-SE"); // YYYY-MM-DD, local

/** Local midnight of a YYYY-MM-DD day (plus ``days``), as ISO. */
const midnight = (day: string, days = 0) => {
  const d = new Date(`${day}T00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString();
};

export function MaintenanceSection() {
  const { t } = useI18n();
  const client = useQueryClient();
  const refresh = () => {
    for (const key of ["events", "detections", "segments", "segment", "status"]) {
      void client.invalidateQueries({ queryKey: [key] });
    }
  };
  const rebuild = useMutation({ mutationFn: api.rebuildEvents, onSuccess: refresh });
  const [camera, setCamera] = useState("");
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const reanalyse = useMutation({
    mutationFn: () =>
      api.reanalyseRange({
        camera: camera || undefined,
        start: midnight(from),
        end: midnight(to, 1), // the whole last day
      }),
    onSuccess: refresh,
  });

  return (
    <section className="form-stack maintenance">
      <Divider className="settings-divider" />
      <Subtitle2 as="h2" className="panel-heading">
        {t("maintenance.title")}
      </Subtitle2>

      <Field label={t("maintenance.rebuild")} hint={t("maintenance.rebuildHint")}>
        <div className="row">
          <ConfirmPopover
            title={t("maintenance.rebuildConfirm")}
            body={t("maintenance.rebuildBody")}
            confirmLabel={t("maintenance.rebuildButton")}
            icon={<ArrowSync20Regular />}
            align="start"
            onConfirm={() => rebuild.mutateAsync()}
          >
            <Button icon={<ArrowSync20Regular />}>{t("maintenance.rebuildButton")}</Button>
          </ConfirmPopover>
        </div>
      </Field>
      {rebuild.data && (
        <MessageBar intent="success">
          <MessageBarBody>{t("maintenance.rebuildDone", rebuild.data)}</MessageBarBody>
        </MessageBar>
      )}

      <Field label={t("maintenance.reanalyse")} hint={t("maintenance.reanalyseHint")}>
        <div className="row wrap">
          <CameraSelect value={camera} onChange={setCamera} />
          {/* The two days stay together when the row wraps (phones). */}
          <span className="row nowrap">
            <DateTimeInput
              type="date"
              value={from}
              placeholder={t("maintenance.from")}
              aria-label={t("maintenance.from")}
              onChange={setFrom}
            />
            <span>–</span>
            <DateTimeInput
              type="date"
              value={to}
              placeholder={t("maintenance.to")}
              aria-label={t("maintenance.to")}
              onChange={setTo}
            />
          </span>
        </div>
      </Field>
      <div className="row">
        <ConfirmPopover
          title={t("maintenance.reanalyseConfirm")}
          body={t("maintenance.reanalyseBody")}
          confirmLabel={t("maintenance.reanalyseButton")}
          icon={<ArrowClockwise20Regular />}
          align="start"
          onConfirm={() => reanalyse.mutateAsync()}
        >
          <Button icon={<ArrowClockwise20Regular />} disabled={!from || !to || to < from}>
            {t("maintenance.reanalyseButton")}
          </Button>
        </ConfirmPopover>
      </div>
      {reanalyse.data && (
        <MessageBar intent="success">
          <MessageBarBody>{t("maintenance.reanalyseDone", reanalyse.data)}</MessageBarBody>
        </MessageBar>
      )}
    </section>
  );
}
