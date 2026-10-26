// Settings → Configuration: what was set up in the web UI as one file (server: backup.py),
// without passwords and tokens, and back.
import { Button, Field, MessageBar, MessageBarBody, Subtitle2 } from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  ArrowUpload20Regular,
  DocumentArrowUp20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, type ImportKind, type ImportReport } from "../api";
import { useI18n, type I18n } from "../i18n";
import { ConfirmPopover } from "./ConfirmPopover";

const KINDS: ImportKind[] = ["settings", "profiles", "cameras", "camera_settings", "notifications"];

function itemText(
  t: I18n["t"],
  i: { kind: ImportKind; id: string; reason: string; detail?: string },
) {
  const reason = t(`backup.reason.${i.reason as "config" | "login" | "secret" | "invalid"}`);
  return `${t(`backup.kind.${i.kind}`)} ${i.id}: ${reason}${i.detail ? ` (${i.detail})` : ""}`;
}

function Result({ report }: { report: ImportReport }) {
  const { t } = useI18n();
  return (
    <>
      <MessageBar intent="success">
        <MessageBarBody>
          {t("backup.imported", Object.fromEntries(KINDS.map((k) => [k, report.imported[k]])))}
        </MessageBarBody>
      </MessageBar>
      {report.attention.length > 0 && (
        <MessageBar intent="warning" layout="multiline">
          <MessageBarBody>
            <strong>{t("backup.attentionTitle")}</strong>
            <ul className="plain-list">
              {report.attention.map((a) => (
                <li key={`${a.kind}/${a.id}`}>{itemText(t, a)}</li>
              ))}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
      {report.skipped.length > 0 && (
        <MessageBar intent="warning" layout="multiline">
          <MessageBarBody>
            <strong>{t("backup.skippedTitle")}</strong>
            <ul className="plain-list">
              {report.skipped.map((s) => (
                <li key={`${s.kind}/${s.id}`}>{itemText(t, s)}</li>
              ))}
            </ul>
          </MessageBarBody>
        </MessageBar>
      )}
    </>
  );
}

export function BackupSection() {
  const { t } = useI18n();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; data: unknown } | null>(null);
  const [badFile, setBadFile] = useState(false);
  const importing = useMutation({
    mutationFn: (data: unknown) => api.importSettings(data),
    onSuccess: () => {
      setFile(null);
      void client.invalidateQueries();
    },
  });

  const pick = (f: File | undefined) => {
    importing.reset();
    setBadFile(false);
    setFile(null);
    if (!f) return;
    void f.text().then((text) => {
      try {
        setFile({ name: f.name, data: JSON.parse(text) });
      } catch {
        setBadFile(true);
      }
    });
  };

  return (
    <section className="form-stack settings-section">
      <Subtitle2 as="h2" className="panel-heading">
        {t("backup.title")}
      </Subtitle2>
      <Field label={t("backup.export")} hint={t("backup.exportHint")}>
        <div className="row">
          <Button
            as="a"
            icon={<ArrowDownload20Regular />}
            href="/api/admin/settings-export"
            download
          >
            {t("backup.export")}
          </Button>
        </div>
      </Field>
      <Field label={t("backup.import")} hint={t("backup.importHint")}>
        <div className="row wrap">
          <input
            ref={input}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = ""; // the same file can be picked again
            }}
          />
          <Button icon={<DocumentArrowUp20Regular />} onClick={() => input.current?.click()}>
            {file ? file.name : t("backup.chooseFile")}
          </Button>
          {file && (
            <ConfirmPopover
              title={t("backup.importConfirm", { file: file.name })}
              body={t("backup.importBody")}
              confirmLabel={t("backup.import")}
              icon={<ArrowUpload20Regular />}
              align="start"
              onConfirm={() => importing.mutateAsync(file.data)}
            >
              <Button appearance="primary" icon={<ArrowUpload20Regular />}>
                {t("backup.import")}
              </Button>
            </ConfirmPopover>
          )}
        </div>
      </Field>
      {badFile && (
        <MessageBar intent="error">
          <MessageBarBody>{t("backup.badFile")}</MessageBarBody>
        </MessageBar>
      )}
      {importing.data && <Result report={importing.data} />}
    </section>
  );
}
