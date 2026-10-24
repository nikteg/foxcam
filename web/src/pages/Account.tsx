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
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import { Key20Regular, LinkDismiss20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { api } from "../api";
import { AUTH_KEY, useMe } from "../auth";
import { Empty, PageHeader, Tag } from "../components/ui";
import { useRoleName } from "../components/ShareDialog";
import { useI18n } from "../i18n";

export function Account() {
  const { t } = useI18n();
  const me = useMe();
  const [params] = useSearchParams();
  const user = me?.user;

  return (
    <>
      {/* Signing out is in the account menu in the top bar. */}
      <PageHeader title={t("account.title")} />
      {params.get("error") && (
        <MessageBar intent="error" className="block">
          <MessageBarBody>{params.get("error")}</MessageBarBody>
        </MessageBar>
      )}
      {!user ? (
        <Body1 className="muted">{t("account.authOff")}</Body1>
      ) : (
        <>
          <ProfileForm />
          <PasswordForm />
          <LinkedAccounts />
          <MyCameras />
        </>
      )}
    </>
  );
}

function ProfileForm() {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const [name, setName] = useState(me?.user?.name ?? "");
  const save = useMutation({
    mutationFn: () => api.updateMe(name),
    onSuccess: () => client.invalidateQueries({ queryKey: AUTH_KEY }),
  });
  return (
    <form
      className="form-stack account-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label={t("login.email")}>
        <Input value={me?.user?.email ?? ""} readOnly />
      </Field>
      <Field label={t("login.name")}>
        <Input value={name} onChange={(_, d) => setName(d.value)} autoComplete="name" />
      </Field>
      {me?.is_admin && (
        <span className="row tags">
          <Tag kind="target">{t("users.admin")}</Tag>
        </span>
      )}
      <div>
        <Button type="submit" disabled={save.isPending || name === (me?.user?.name ?? "")}>
          {save.isSuccess ? t("account.saved") : t("common.save")}
        </Button>
      </div>
    </form>
  );
}

function PasswordForm() {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const hasPassword = me?.user?.has_password ?? false;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api.changePassword(hasPassword ? current : null, next),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      void client.invalidateQueries({ queryKey: AUTH_KEY });
    },
  });
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("login.password")}
      </Subtitle2>
      <form
        className="form-stack account-form"
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate();
        }}
      >
        {!hasPassword && <Caption1 className="muted">{t("account.noPassword")}</Caption1>}
        {hasPassword && (
          <Field label={t("account.currentPassword")} required>
            <Input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(_, d) => setCurrent(d.value)}
            />
          </Field>
        )}
        <Field label={t("account.newPassword")} hint={t("account.passwordHint")} required>
          <Input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(_, d) => setNext(d.value)}
          />
        </Field>
        {change.error && (
          <MessageBar intent="error">
            <MessageBarBody>{change.error.message}</MessageBarBody>
          </MessageBar>
        )}
        {change.isSuccess && (
          <MessageBar intent="success">
            <MessageBarBody>{t("account.passwordChanged")}</MessageBarBody>
          </MessageBar>
        )}
        <div>
          <Button
            type="submit"
            disabled={next.length < 8 || (hasPassword && !current) || change.isPending}
          >
            {hasPassword ? t("account.changePassword") : t("account.setPassword")}
          </Button>
        </div>
      </form>
    </>
  );
}

function LinkedAccounts() {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const unlink = useMutation({
    mutationFn: api.unlinkProvider,
    onSuccess: () => client.invalidateQueries({ queryKey: AUTH_KEY }),
  });
  const providers = me?.providers ?? [];
  if (!providers.length) return null;
  const linked = new Set(me?.user?.providers ?? []);
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("account.linked")}
      </Subtitle2>
      <p className="muted">{t("account.linkedHint")}</p>
      <div className="cards">
        {providers.map((p) => (
          <Card key={p.id} size="small">
            <CardHeader
              header={<Text weight="semibold">{p.name}</Text>}
              description={
                <Caption1 className="muted">
                  {linked.has(p.id) ? t("account.connected") : t("account.notConnected")}
                </Caption1>
              }
              action={
                linked.has(p.id) ? (
                  <Button
                    size="small"
                    icon={<LinkDismiss20Regular />}
                    disabled={unlink.isPending}
                    onClick={() => unlink.mutate(p.id)}
                  >
                    {t("account.disconnect")}
                  </Button>
                ) : (
                  <Button
                    size="small"
                    as="a"
                    icon={<Key20Regular />}
                    href={`/api/auth/oauth/${encodeURIComponent(p.id)}/start?link=true`}
                  >
                    {t("account.connect")}
                  </Button>
                )
              }
            />
          </Card>
        ))}
      </div>
      {unlink.error && (
        <MessageBar intent="error" className="block">
          <MessageBarBody>{unlink.error.message}</MessageBarBody>
        </MessageBar>
      )}
    </>
  );
}

/** Cameras shared with this user, with a way to stop seeing one. */
function MyCameras() {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const roleName = useRoleName();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const leave = useMutation({
    mutationFn: (cameraId: string) => api.unshare(cameraId, me?.user?.id ?? 0),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cameras"] }),
  });
  if (me?.is_admin) return null;
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("account.myCameras")}
      </Subtitle2>
      {cameras.data?.length === 0 && <Empty>{t("account.noCameras")}</Empty>}
      <div className="cards">
        {cameras.data?.map((c) => (
          <Card key={c.id} size="small">
            <CardHeader
              header={<Text weight="semibold">{c.name}</Text>}
              description={<Caption1 className="muted">{roleName(c.role)}</Caption1>}
              action={
                c.role !== "owner" ? (
                  <Button
                    size="small"
                    disabled={leave.isPending}
                    onClick={() => leave.mutate(c.id)}
                  >
                    {t("account.leave")}
                  </Button>
                ) : undefined
              }
            />
          </Card>
        ))}
      </div>
    </>
  );
}
