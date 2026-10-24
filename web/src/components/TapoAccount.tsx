import {
  Body1,
  Button,
  Caption1,
  Card,
  CardHeader,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Text,
} from "@fluentui/react-components";
import {
  Add20Regular,
  ArrowLeft20Regular,
  Cloud24Regular,
  PlugConnected20Regular,
  SignOut20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { api } from "../api";
import { useI18n } from "../i18n";
import { ActionRow, Tag } from "./ui";

const INSTALL_COMMAND = "uv sync --all-packages --extra tapo-cloud";

export function TapoNotInstalled() {
  const { rich } = useI18n();
  return (
    <MessageBar intent="warning">
      <MessageBarBody>
        {rich("tapo.notInstalled", { command: <code>{INSTALL_COMMAND}</code> })}
      </MessageBarBody>
    </MessageBar>
  );
}

/** Cameras on the signed-in Tapo account, each with an Add button (or where it was added). */
export function TapoCameraList() {
  const { t } = useI18n();
  const client = useQueryClient();
  const devices = useQuery({ queryKey: ["tapo", "devices"], queryFn: api.tapoDevices });
  const [resolution, setResolution] = useState<"HD" | "VGA">("HD");
  const add = useMutation({
    mutationFn: api.addCamera,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cameras"] });
      void client.invalidateQueries({ queryKey: ["status"] });
      void client.invalidateQueries({ queryKey: ["tapo", "devices"] });
    },
  });
  const error = devices.error ?? add.error;

  return (
    <div className="form-stack">
      {devices.isPending && (
        <Spinner size="small" label={t("addCamera.findingCameras")} className="loading-block" />
      )}
      {error && (
        <MessageBar intent="error">
          <MessageBarBody>{error.message}</MessageBarBody>
        </MessageBar>
      )}
      {devices.data?.length === 0 && <Body1>{t("addCamera.noTapoCameras")}</Body1>}
      {devices.data && devices.data.length > 0 && (
        <>
          <Field label={t("addCamera.streamQuality")} hint={t("addCamera.tapoWarning")}>
            <Select value={resolution} onChange={(_, d) => setResolution(d.value as "HD" | "VGA")}>
              <option value="HD">{t("addCamera.qualityHd")}</option>
              <option value="VGA">{t("addCamera.qualityVga")}</option>
            </Select>
          </Field>
          <ul className="device-list">
            {devices.data.map((d) => (
              <li key={d.device_id}>
                <span className="device-name">
                  <Text weight="semibold">{d.name}</Text>
                  <Caption1 className="muted">{d.model}</Caption1>
                </span>
                {d.camera_id ? (
                  <Tag kind="ok">{t("addCamera.addedAs", { id: d.camera_id })}</Tag>
                ) : (
                  <Button
                    icon={<Add20Regular />}
                    disabled={add.isPending}
                    onClick={() =>
                      add.mutate({
                        name: d.name,
                        source: {
                          type: "tapo_cloud",
                          device_id: d.device_id,
                          device_name: d.name,
                          resolution,
                        },
                      })
                    }
                  >
                    {t("common.add")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** Sign in to the Tapo cloud account, including the e-mail/app verification code step. */
export function TapoSignIn({
  onConnected,
  back,
}: {
  onConnected?: () => void;
  /** Leave this step (e.g. the Add camera dialog's "Other source types"). */
  back?: ReactNode;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfa, setMfa] = useState<{ loginId: string; delivery: "email" | "app" } | null>(null);

  const done = () => {
    setPassword("");
    void client.invalidateQueries({ queryKey: ["tapo"] });
    onConnected?.();
  };
  const login = useMutation({
    mutationFn: () => api.tapoLogin(email, password),
    onSuccess: (r) =>
      r.status === "mfa_required" ? setMfa({ loginId: r.login_id, delivery: r.delivery }) : done(),
  });
  const verify = useMutation({
    mutationFn: () => api.tapoMfa(mfa!.loginId, code),
    onSuccess: done,
  });
  const error = (mfa ? verify.error : login.error) as Error | null;

  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (mfa) verify.mutate();
        else login.mutate();
      }}
    >
      {!mfa ? (
        <>
          <Body1 className="muted">{t("tapo.signInIntro")}</Body1>
          <Field label={t("tapo.email")} required>
            <Input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(_, d) => setEmail(d.value)}
            />
          </Field>
          <Field label={t("tapo.password")} required>
            <Input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(_, d) => setPassword(d.value)}
            />
          </Field>
        </>
      ) : (
        <Field
          label={t("tapo.code")}
          hint={mfa.delivery === "email" ? t("tapo.codeSentEmail") : t("tapo.codeSentApp")}
          required
        >
          <Input
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(_, d) => setCode(d.value)}
          />
        </Field>
      )}
      {error && (
        <MessageBar intent="error">
          <MessageBarBody>{error.message}</MessageBarBody>
        </MessageBar>
      )}
      <ActionRow
        back={
          (back || mfa) && (
            <>
              {back}
              {mfa && (
                <Button
                  appearance="subtle"
                  icon={<ArrowLeft20Regular />}
                  onClick={() => setMfa(null)}
                >
                  {t("tapo.startOver")}
                </Button>
              )}
            </>
          )
        }
      >
        {(login.isPending || verify.isPending) && <Spinner size="tiny" />}
        <Button
          type="submit"
          appearance="primary"
          icon={<PlugConnected20Regular />}
          disabled={mfa ? !code || verify.isPending : !email || !password || login.isPending}
        >
          {mfa ? t("tapo.verify") : t("tapo.signIn")}
        </Button>
      </ActionRow>
    </form>
  );
}

export function TapoAccountCard() {
  const { t, rich } = useI18n();
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["tapo", "status"], queryFn: api.tapoStatus });
  const logout = useMutation({
    mutationFn: api.tapoLogout,
    onSuccess: () => client.invalidateQueries({ queryKey: ["tapo"] }),
  });
  const s = status.data;

  return (
    <Card className="integration-card">
      <CardHeader
        image={<Cloud24Regular />}
        header={<Text weight="semibold">{t("tapo.account")}</Text>}
        description={<Caption1 className="muted">{t("tapo.accountDescription")}</Caption1>}
        action={
          s &&
          (s.expired ? (
            <Tag kind="bad">{t("tapo.expiredTag")}</Tag>
          ) : s.connected ? (
            <Tag kind="ok">{t("tapo.connected")}</Tag>
          ) : s.available ? (
            <Tag>{t("tapo.notConnected")}</Tag>
          ) : (
            <Tag kind="bad">{t("tapo.notInstalledTag")}</Tag>
          ))
        }
      />
      {s && !s.available && <TapoNotInstalled />}
      {s?.available &&
        (s.expired ? (
          <>
            <MessageBar intent="warning">
              <MessageBarBody>
                {rich("tapo.expired", { email: <b>{s.email ?? "?"}</b> })}
              </MessageBarBody>
            </MessageBar>
            <TapoSignIn />
          </>
        ) : s.connected ? (
          <>
            <div className="row wrap">
              <Body1>{rich("tapo.signedInAs", { email: <b>{s.email ?? "?"}</b> })}</Body1>
              <Button
                size="small"
                icon={<SignOut20Regular />}
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
              >
                {t("tapo.signOut")}
              </Button>
            </div>
            <TapoCameraList />
          </>
        ) : (
          <TapoSignIn />
        ))}
    </Card>
  );
}
