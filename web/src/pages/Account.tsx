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
  MessageBarTitle,
  Select,
  Subtitle2,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from "@fluentui/react-components";
import {
  Add20Regular,
  Delete20Regular,
  Key20Regular,
  LinkDismiss20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { api, type ApiKeyScope } from "../api";
import { AUTH_KEY, useMe } from "../auth";
import { CopyBlock } from "../components/AddWorkerDialog";
import { AppearanceSection } from "../components/AppearanceSection";
import { ConfirmPopover } from "../components/ConfirmPopover";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";
import { Checkbox, Empty, PageTitle, PasswordInput, Tag, Time } from "../components/ui";
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
      <PageTitle title={t("account.title")} />
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
          <ApiKeys />
        </>
      )}
      <AppearanceSection />
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
            <PasswordInput
              autoComplete="current-password"
              value={current}
              onChange={(_, d) => setCurrent(d.value)}
            />
          </Field>
        )}
        <Field label={t("account.newPassword")} hint={t("account.passwordHint")} required>
          <PasswordInput
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
                  <ConfirmPopover
                    title={t("account.disconnectTitle", { name: p.name })}
                    body={t("account.disconnectBody")}
                    confirmLabel={t("account.disconnect")}
                    icon={<LinkDismiss20Regular />}
                    onConfirm={() => unlink.mutateAsync(p.id)}
                  >
                    <Button size="small" icon={<LinkDismiss20Regular />}>
                      {t("account.disconnect")}
                    </Button>
                  </ConfirmPopover>
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

const EXPIRY_DAYS = [null, 30, 90, 365] as const;

/** Keys for scripts and other programs, acting as this user. */
function ApiKeys() {
  const { t, rich } = useI18n();
  const client = useQueryClient();
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: api.apiKeys });
  const [adding, setAdding] = useState(false);
  const revoke = useMutation({
    mutationFn: api.revokeApiKey,
    onSuccess: () => client.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("apiKeys.title")}
      </Subtitle2>
      <p className="muted">
        {rich("apiKeys.intro", { header: <code>Authorization: Bearer msk_…</code> })}
      </p>
      {keys.data?.length === 0 && <Empty>{t("apiKeys.none")}</Empty>}
      {!!keys.data?.length && (
        <Table className="table responsive" aria-label={t("apiKeys.title")}>
          <TableHeader>
            <TableRow>
              <TableHeaderCell>{t("apiKeys.name")}</TableHeaderCell>
              <TableHeaderCell>{t("apiKeys.colScopes")}</TableHeaderCell>
              <TableHeaderCell>{t("apiKeys.colKey")}</TableHeaderCell>
              <TableHeaderCell>{t("apiKeys.colLastUsed")}</TableHeaderCell>
              <TableHeaderCell>{t("apiKeys.expiry")}</TableHeaderCell>
              <TableHeaderCell aria-label={t("col.actions")} className="col-actions" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.data.map((k) => (
              <TableRow key={k.id}>
                <TableCell data-label={t("apiKeys.name")}>
                  <Text weight="semibold">{k.name}</Text>
                </TableCell>
                <TableCell data-label={t("apiKeys.colScopes")}>
                  <span className="chips">
                    {k.scopes.map((s) => (
                      <Tag key={s} kind={s === "admin" ? "target" : undefined}>
                        {t(`apiKeys.scope.${s}`)}
                      </Tag>
                    ))}
                  </span>
                </TableCell>
                <TableCell data-label={t("apiKeys.colKey")}>
                  <code>{k.prefix}…</code>
                </TableCell>
                <TableCell data-label={t("apiKeys.colLastUsed")}>
                  {k.last_used_at ? (
                    <Time iso={k.last_used_at} relative />
                  ) : (
                    <span className="muted">{t("apiKeys.never")}</span>
                  )}
                </TableCell>
                <TableCell data-label={t("apiKeys.expiry")}>
                  {k.expires_at ? (
                    <Time iso={k.expires_at} />
                  ) : (
                    <span className="muted">{t("apiKeys.noExpiry")}</span>
                  )}
                </TableCell>
                <TableCell className="full">
                  <ConfirmPopover
                    title={t("apiKeys.revokeConfirm", { name: k.name })}
                    confirmLabel={t("apiKeys.revoke")}
                    onConfirm={() => revoke.mutateAsync(k.id)}
                  >
                    <Button size="small" icon={<Delete20Regular />}>
                      {t("apiKeys.revoke")}
                    </Button>
                  </ConfirmPopover>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <div className="section-actions">
        <Button icon={<Add20Regular />} onClick={() => setAdding(true)}>
          {t("apiKeys.add")}
        </Button>
      </div>
      <AddApiKeyDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function AddApiKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [write, setWrite] = useState(true);
  const [admin, setAdmin] = useState(false);
  const [expires, setExpires] = useState("");
  const scopes: ApiKeyScope[] = [
    "read",
    ...(write ? ["write" as const] : []),
    ...(admin ? ["admin" as const] : []),
  ];
  const create = useMutation({
    mutationFn: () =>
      api.createApiKey({
        name: name.trim(),
        scopes,
        expires_days: expires ? Number(expires) : null,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  const close = () => {
    create.reset();
    setName("");
    setWrite(true);
    setAdmin(false);
    setExpires("");
    onClose();
  };
  const created = create.data;
  return (
    <Dialog open={open} onOpenChange={(_, d) => !d.open && close()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("apiKeys.add")}</DialogTitle>
          <DialogContent className="form-stack">
            {!created ? (
              <form
                id="add-api-key"
                className="form-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  create.mutate();
                }}
              >
                <Field label={t("apiKeys.name")} hint={t("apiKeys.nameHint")} required>
                  <Input value={name} maxLength={80} onChange={(_, d) => setName(d.value)} />
                </Field>
                <Field label={t("apiKeys.scopes")}>
                  <div className="form-stack">
                    <Checkbox checked disabled label={t("apiKeys.scope.readHint")} />
                    <Checkbox
                      checked={write}
                      onChange={(_, d) => setWrite(d.checked === true)}
                      label={t("apiKeys.scope.writeHint")}
                    />
                    {me?.is_admin && (
                      <Checkbox
                        checked={admin}
                        onChange={(_, d) => setAdmin(d.checked === true)}
                        label={t("apiKeys.scope.adminHint")}
                      />
                    )}
                  </div>
                </Field>
                <Field label={t("apiKeys.expiry")}>
                  <Select value={expires} onChange={(_, d) => setExpires(d.value)}>
                    {EXPIRY_DAYS.map((days) => (
                      <option key={days ?? "never"} value={days ?? ""}>
                        {days ? t("profiles.days", { n: days }) : t("apiKeys.noExpiry")}
                      </option>
                    ))}
                  </Select>
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
                    <MessageBarTitle>{t("apiKeys.once")}</MessageBarTitle>
                    {t("apiKeys.onceBody")}
                  </MessageBarBody>
                </MessageBar>
                <CopyBlock text={created.key} label={t("apiKeys.title")} />
              </>
            )}
          </DialogContent>
          <DialogActions>
            {!created ? (
              <>
                <Button onClick={close}>{t("common.cancel")}</Button>
                <Button
                  type="submit"
                  form="add-api-key"
                  appearance="primary"
                  icon={<Key20Regular />}
                  disabled={!name.trim() || create.isPending}
                >
                  {t("apiKeys.create")}
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
