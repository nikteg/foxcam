import {
  Button,
  Caption1,
  CompoundButton,
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
  Select,
  Spinner,
} from "@fluentui/react-components";
import {
  Add20Regular,
  ArrowLeft20Regular,
  Cloud24Regular,
  Dismiss24Regular,
  Document24Regular,
  Globe24Regular,
  PlugConnected20Regular,
  Video24Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";

import { api, type CameraSource, type ProbeResult } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import { TapoCameraList, TapoNotInstalled, TapoSignIn } from "./TapoAccount";

type Kind = "tapo" | "rtsp" | "file" | "test";

const TITLES: Record<Kind, MessageKey> = {
  tapo: "addCamera.kind.tapo",
  rtsp: "addCamera.kind.rtsp",
  file: "addCamera.kind.file",
  test: "addCamera.kind.test",
};

export function AddCameraDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const [kind, setKind] = useState<Kind | null>(null);
  const tapoConnected = useTapoConnected();
  const close = () => {
    setKind(null);
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(_, d) => !d.open && close()}>
      <DialogSurface className="add-camera-dialog">
        <DialogBody>
          <DialogTitle
            action={
              <Button
                appearance="subtle"
                aria-label={t("common.close")}
                icon={<Dismiss24Regular />}
                onClick={close}
              />
            }
          >
            {kind ? t("addCamera.titleKind", { kind: t(TITLES[kind]) }) : t("addCamera.title")}
          </DialogTitle>
          <DialogContent>
            {kind === null && <ChooseKind onChoose={setKind} />}
            {kind === "tapo" && <TapoStep />}
            {kind === "rtsp" && <RtspStep onDone={close} />}
            {kind === "file" && <FileStep onDone={close} />}
            {kind === "test" && <TestStep onDone={close} />}
          </DialogContent>
          {kind !== null && (
            <DialogActions position="start">
              <Button
                appearance="subtle"
                icon={<ArrowLeft20Regular />}
                onClick={() => setKind(null)}
              >
                {t("addCamera.otherTypes")}
              </Button>
            </DialogActions>
          )}
          {kind === "tapo" && tapoConnected && (
            <DialogActions position="end">
              <Button appearance="primary" onClick={close}>
                {t("common.done")}
              </Button>
            </DialogActions>
          )}
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function ChooseKind({ onChoose }: { onChoose: (k: Kind) => void }) {
  const { t } = useI18n();
  const option = (kind: Kind, icon: ReactElement, secondary: MessageKey) => (
    <CompoundButton
      className="kind-option"
      icon={icon}
      secondaryContent={t(secondary)}
      onClick={() => onChoose(kind)}
    >
      {t(TITLES[kind])}
    </CompoundButton>
  );
  return (
    <div className="kind-grid">
      {option("tapo", <Cloud24Regular />, "addCamera.kind.tapoHelp")}
      {option("rtsp", <Globe24Regular />, "addCamera.kind.rtspHelp")}
      {option("file", <Document24Regular />, "addCamera.kind.fileHelp")}
      {option("test", <Video24Regular />, "addCamera.kind.testHelp")}
    </div>
  );
}

/** Shared "add" mutation: refreshes everything that lists cameras, then calls onDone. */
function useAddCamera(onDone?: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.addCamera,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["status"] });
      void client.invalidateQueries({ queryKey: ["tapo", "devices"] });
      onDone?.();
    },
  });
}

function SegmentField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useI18n();
  return (
    <Field label={t("camera.segmentLengthSeconds")} hint={t("camera.segmentLengthHint")}>
      <Input
        type="number"
        min={5}
        max={3600}
        placeholder={t("camera.default")}
        value={value}
        onChange={(_, d) => onChange(d.value)}
      />
    </Field>
  );
}

const segmentValue = (v: string) => (v ? Number(v) : null);

function ErrorBar({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <MessageBar intent="error">
      <MessageBarBody>{(error as Error).message}</MessageBarBody>
    </MessageBar>
  );
}

function ProbeBar({ result }: { result: ProbeResult | undefined }) {
  const { t } = useI18n();
  if (!result) return null;
  return (
    <MessageBar intent={result.ok ? "success" : "error"}>
      <MessageBarBody>
        {t(result.ok ? "addCamera.probeOk" : "addCamera.probeFailed", { detail: result.detail })}
      </MessageBarBody>
    </MessageBar>
  );
}

// --- Tapo cloud -------------------------------------------------------------------

function TapoStep() {
  const status = useQuery({ queryKey: ["tapo", "status"], queryFn: api.tapoStatus });
  const connected = (status.data?.connected && !status.data.expired) ?? false;

  if (status.isPending) return <Spinner size="small" className="loading-block" />;
  if (status.data && !status.data.available) return <TapoNotInstalled />;
  if (!connected) return <TapoSignIn />;

  return <TapoCameraList />;
}

/** Signed in to Tapo: the camera list is showing and "Done" closes the dialog. */
function useTapoConnected(): boolean {
  const status = useQuery({ queryKey: ["tapo", "status"], queryFn: api.tapoStatus });
  return (status.data?.available && status.data.connected && !status.data.expired) ?? false;
}

// --- RTSP ---------------------------------------------------------------------------

function RtspStep({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [segment, setSegment] = useState("");
  const [tapo, setTapo] = useState({ host: "", user: "", password: "", stream: "stream1" });
  const add = useAddCamera(onDone);
  const probe = useMutation({ mutationFn: (source: CameraSource) => api.probeSource(source) });
  const source: CameraSource = { type: "rtsp", url: url.trim() };

  const buildTapoUrl = () => {
    if (!tapo.host) return;
    const auth = tapo.user
      ? `${encodeURIComponent(tapo.user)}:${encodeURIComponent(tapo.password)}@`
      : "";
    setUrl(`rtsp://${auth}${tapo.host.trim()}:554/${tapo.stream}`);
    probe.reset();
  };

  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate({ name, source, segment_seconds: segmentValue(segment) });
      }}
    >
      <Field label={t("camera.name")} required>
        <Input
          value={name}
          placeholder={t("addCamera.namePlaceholder")}
          onChange={(_, d) => setName(d.value)}
        />
      </Field>
      <Field label={t("addCamera.rtspUrl")} required hint={t("addCamera.rtspUrlHint")}>
        <Input
          value={url}
          placeholder="rtsp://user:password@192.168.1.50:554/stream1"
          onChange={(_, d) => {
            setUrl(d.value);
            probe.reset();
          }}
        />
      </Field>

      <details className="helper">
        <summary>{t("addCamera.tapoHelper")}</summary>
        <Caption1 className="muted">{t("addCamera.tapoHelperIntro")}</Caption1>
        <div className="helper-grid">
          <Field label={t("addCamera.cameraIp")}>
            <Input
              value={tapo.host}
              placeholder="192.168.1.50"
              onChange={(_, d) => setTapo({ ...tapo, host: d.value })}
            />
          </Field>
          <Field label={t("addCamera.quality")}>
            <Select value={tapo.stream} onChange={(_, d) => setTapo({ ...tapo, stream: d.value })}>
              <option value="stream1">{t("addCamera.streamHd")}</option>
              <option value="stream2">{t("addCamera.streamSd")}</option>
            </Select>
          </Field>
          <Field label={t("addCamera.accountUser")}>
            <Input value={tapo.user} onChange={(_, d) => setTapo({ ...tapo, user: d.value })} />
          </Field>
          <Field label={t("addCamera.accountPassword")}>
            <Input
              type="password"
              value={tapo.password}
              onChange={(_, d) => setTapo({ ...tapo, password: d.value })}
            />
          </Field>
        </div>
        <Button size="small" disabled={!tapo.host} onClick={buildTapoUrl}>
          {t("addCamera.useThisCamera")}
        </Button>
      </details>

      <SegmentField value={segment} onChange={setSegment} />
      <ProbeBar result={probe.data} />
      <ErrorBar error={probe.error ?? add.error} />
      <div className="row">
        <Button
          icon={<PlugConnected20Regular />}
          disabled={!url || probe.isPending}
          onClick={() => probe.mutate(source)}
        >
          {t("addCamera.testConnection")}
        </Button>
        {probe.isPending && <Spinner size="tiny" />}
        <Button
          type="submit"
          appearance="primary"
          icon={<Add20Regular />}
          disabled={!name || !url || add.isPending}
        >
          {t("addCamera.add")}
        </Button>
      </div>
    </form>
  );
}

// --- File & test pattern ------------------------------------------------------------

function FileStep({ onDone }: { onDone: () => void }) {
  const { t, rich } = useI18n();
  const files = useQuery({ queryKey: ["sample-files"], queryFn: api.sampleFiles });
  const [file, setFile] = useState("");
  const [name, setName] = useState("");
  const [segment, setSegment] = useState("");
  const add = useAddCamera(onDone);
  const chosen = file || files.data?.[0] || "";

  if (files.data?.length === 0) {
    return (
      <MessageBar intent="info">
        <MessageBarBody>
          {rich("addCamera.noSamples", { command: <code>python3 scripts/fetch_samples.py</code> })}
        </MessageBarBody>
      </MessageBar>
    );
  }
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate({
          name: name || chosen.replace(/\.\w+$/, ""),
          source: { type: "file", path: chosen },
          segment_seconds: segmentValue(segment),
        });
      }}
    >
      <Field label={t("addCamera.video")} required>
        <Select value={chosen} onChange={(_, d) => setFile(d.value)}>
          {files.data?.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("camera.name")}>
        <Input
          value={name}
          placeholder={chosen.replace(/\.\w+$/, "")}
          onChange={(_, d) => setName(d.value)}
        />
      </Field>
      <SegmentField value={segment} onChange={setSegment} />
      <ErrorBar error={add.error} />
      <div className="row">
        <Button type="submit" appearance="primary" icon={<Add20Regular />} disabled={!chosen}>
          {t("addCamera.add")}
        </Button>
      </div>
    </form>
  );
}

function TestStep({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(() => t("addCamera.kind.test"));
  const add = useAddCamera(onDone);
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate({ name, source: { type: "test" } });
      }}
    >
      <Field label={t("camera.name")} required>
        <Input value={name} onChange={(_, d) => setName(d.value)} />
      </Field>
      <ErrorBar error={add.error} />
      <div className="row">
        <Button type="submit" appearance="primary" icon={<Add20Regular />} disabled={!name}>
          {t("addCamera.add")}
        </Button>
      </div>
    </form>
  );
}
