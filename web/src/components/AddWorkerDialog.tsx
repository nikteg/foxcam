import {
  Body1,
  Button,
  Caption1,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Select,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import { Checkmark16Regular, Copy16Regular, Key20Regular } from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { TagsPicker } from "./LabelPicker";
import {
  guessPlatform,
  PLATFORMS,
  setupSteps,
  type DetectorChoice,
  type Platform,
} from "../workerSetup";

const DETECTOR_LABELS: Record<DetectorChoice, MessageKey> = {
  speciesnet: "workers.detector.speciesnet",
  ultralytics: "workers.detector.ultralytics",
  dummy: "workers.detector.dummy",
};

const PLATFORM_LABELS: Record<Platform, MessageKey> = {
  wsl: "workers.platform.wsl",
  windows: "workers.platform.windows",
  linux: "workers.platform.linux",
  docker: "workers.platform.docker",
};

/** Clipboard API needs a secure context (https or localhost); fall back to execCommand. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

function CopyBlock({ text, label }: { text: string; label: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-block">
      <pre aria-label={label}>{text}</pre>
      <Tooltip content={copied ? t("workers.copied") : t("workers.copy")} relationship="label">
        <Button
          size="small"
          appearance="subtle"
          icon={copied ? <Checkmark16Regular /> : <Copy16Regular />}
          onClick={async () => {
            setCopied(await copyText(text));
            setTimeout(() => setCopied(false), 2000);
          }}
        />
      </Tooltip>
    </div>
  );
}

export function AddWorkerDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, rich } = useI18n();
  const client = useQueryClient();
  const [name, setName] = useState("gpu-box");
  const [detector, setDetector] = useState<DetectorChoice>("speciesnet");
  const [platform, setPlatform] = useState<Platform>(guessPlatform);
  const [tags, setTags] = useState<string[]>([]);
  const create = useMutation({
    mutationFn: () => api.createWorkerToken(name.trim(), detector, tags),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workers"] });
      void client.invalidateQueries({ queryKey: ["queue"] });
    },
  });
  const close = () => {
    create.reset();
    onClose();
  };
  const server = window.location.origin;
  const isLocalhost = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  const created = create.data;

  return (
    <Dialog open={open} onOpenChange={(_, d) => !d.open && close()}>
      <DialogSurface className="add-camera-dialog">
        <DialogBody>
          <DialogTitle>{t("workers.addTitle")}</DialogTitle>
          <DialogContent className="form-stack">
            {!created ? (
              <form
                id="add-worker"
                className="form-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  create.mutate();
                }}
              >
                <Body1 className="muted">{t("workers.addIntro")}</Body1>
                <Field label={t("workers.name")} hint={t("workers.nameHint")} required>
                  <Input value={name} onChange={(_, d) => setName(d.value)} />
                </Field>
                <Field label={t("workers.detector")} hint={t("workers.detectorHint")}>
                  <Select
                    value={detector}
                    onChange={(_, d) => setDetector(d.value as DetectorChoice)}
                  >
                    {(Object.keys(DETECTOR_LABELS) as DetectorChoice[]).map((d) => (
                      <option key={d} value={d}>
                        {t(DETECTOR_LABELS[d])}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("workers.tags")} hint={t("workers.tagsHint")}>
                  <TagsPicker
                    value={tags}
                    onChange={setTags}
                    placeholder="gpu"
                    ariaLabel={t("workers.tags")}
                  />
                </Field>
                {create.error && (
                  <MessageBar intent="error">
                    <MessageBarBody>{create.error.message}</MessageBarBody>
                  </MessageBar>
                )}
              </form>
            ) : (
              <>
                <MessageBar intent="warning">
                  <MessageBarBody>
                    <MessageBarTitle>{t("workers.tokenOnce")}</MessageBarTitle>
                    {t("workers.tokenOnceBody")}
                  </MessageBarBody>
                </MessageBar>
                <Text weight="semibold">{t("workers.token")}</Text>
                <CopyBlock text={created.token} label={t("workers.token")} />

                <Text weight="semibold">{t("workers.setupTitle")}</Text>
                <Field label={t("workers.platform")}>
                  <Select value={platform} onChange={(_, d) => setPlatform(d.value as Platform)}>
                    {PLATFORMS.map((p) => (
                      <option key={p} value={p}>
                        {t(PLATFORM_LABELS[p])}
                      </option>
                    ))}
                  </Select>
                </Field>
                {platform === "windows" && detector === "speciesnet" && (
                  <MessageBar intent="warning">
                    <MessageBarBody>{t("workers.speciesnetWindows")}</MessageBarBody>
                  </MessageBar>
                )}
                <ol className="setup-steps">
                  {setupSteps(platform, detector, server, created.token).map((step) => (
                    <li key={step.text}>
                      {rich(step.text, { folder: <code>worker/</code> })}
                      {step.code && <CopyBlock text={step.code} label={t(step.text)} />}
                    </li>
                  ))}
                </ol>
                <Caption1 className="muted">
                  {rich("workers.moreHelp", { doc: <code>docs/WORKER_SETUP.md</code> })}
                </Caption1>
                {isLocalhost && (
                  <MessageBar intent="info">
                    <MessageBarBody>{t("workers.localhostHint")}</MessageBarBody>
                  </MessageBar>
                )}
              </>
            )}
          </DialogContent>
          <DialogActions>
            {!created ? (
              <>
                <Button onClick={close}>{t("common.cancel")}</Button>
                <Button
                  type="submit"
                  form="add-worker"
                  appearance="primary"
                  icon={<Key20Regular />}
                  disabled={!name.trim() || create.isPending}
                >
                  {t("workers.createToken")}
                </Button>
              </>
            ) : (
              <Button appearance="primary" onClick={close}>
                {t("common.done")}
              </Button>
            )}
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
