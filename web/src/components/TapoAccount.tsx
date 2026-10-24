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
  Spinner,
  Text,
} from "@fluentui/react-components";
import { Cloud24Regular, PlugConnected20Regular, SignOut20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useI18n } from "../i18n";
import { Tag } from "./ui";

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

/** Sign in to the Tapo cloud account, including the e-mail/app verification code step. */
export function TapoSignIn({ onConnected }: { onConnected?: () => void }) {
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
      <div className="row">
        <Button
          type="submit"
          appearance="primary"
          icon={<PlugConnected20Regular />}
          disabled={mfa ? !code || verify.isPending : !email || !password || login.isPending}
        >
          {mfa ? t("tapo.verify") : t("tapo.signIn")}
        </Button>
        {(login.isPending || verify.isPending) && <Spinner size="tiny" />}
        {mfa && (
          <Button appearance="subtle" onClick={() => setMfa(null)}>
            {t("tapo.startOver")}
          </Button>
        )}
      </div>
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
          (s.connected ? (
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
        (s.connected ? (
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
        ) : (
          <TapoSignIn />
        ))}
    </Card>
  );
}
