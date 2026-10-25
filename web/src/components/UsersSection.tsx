import {
  Button,
  Caption1,
  Card,
  CardFooter,
  CardHeader,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import {
  Delete20Regular,
  Key20Regular,
  Link20Regular,
  PersonAdd20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type User } from "../api";
import { useMe } from "../auth";
import { ConfirmPopover } from "./ConfirmPopover";
import { useI18n } from "../i18n";
import { InviteLink, useRoleName } from "./ShareDialog";
import {
  Checkbox,
  DrawerTitle,
  Empty,
  Fact,
  Facts,
  InfoBanner,
  PasswordInput,
  QueryState,
  Tag,
  Time,
  Toolbar,
} from "./ui";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";

/** Settings → Users (admins): accounts and invite links. */
export function UsersSection() {
  const client = useQueryClient();
  const { t } = useI18n();
  const me = useMe();
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [adding, setAdding] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [passwordFor, setPasswordFor] = useState<User | null>(null);

  return (
    <section id="users">
      <Toolbar>
        <Button appearance="primary" icon={<PersonAdd20Regular />} onClick={() => setAdding(true)}>
          {t("users.add")}
        </Button>
        <Button icon={<Link20Regular />} onClick={() => setInviting(true)}>
          {t("users.invite")}
        </Button>
      </Toolbar>
      <InfoBanner id={`users-${me?.registration ?? "invite"}`}>
        {me?.registration === "open"
          ? t("users.introOpen")
          : me?.registration === "closed"
            ? t("users.introClosed")
            : t("users.introInvite")}
      </InfoBanner>
      <QueryState error={users.error} loading={users.isPending} />
      <div className="cards">
        {users.data?.map((u) => (
          <UserCard
            key={u.id}
            user={u}
            self={u.id === me?.user?.id}
            onPassword={() => setPasswordFor(u)}
            onRemove={() =>
              api.deleteUser(u.id).then(() => client.invalidateQueries({ queryKey: ["users"] }))
            }
          />
        ))}
      </div>
      <InvitesList />
      {adding && <AddUserDialog onClose={() => setAdding(false)} />}
      {inviting && <InviteDialog onClose={() => setInviting(false)} />}
      {passwordFor && <PasswordDialog user={passwordFor} onClose={() => setPasswordFor(null)} />}
    </section>
  );
}

function UserCard({
  user: u,
  self,
  onPassword,
  onRemove,
}: {
  user: User;
  self: boolean;
  onPassword: () => void;
  onRemove: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const update = useMutation({
    mutationFn: (body: { is_admin?: boolean; disabled?: boolean }) => api.updateUser(u.id, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["users"] }),
  });
  return (
    <Card>
      <CardHeader
        header={<Text weight="semibold">{u.display_name}</Text>}
        description={
          <span className="row wrap tags">
            {u.name && <Caption1 className="muted">{u.email}</Caption1>}
            {u.is_admin && <Tag kind="target">{t("users.admin")}</Tag>}
            {u.disabled && <Tag kind="bad">{t("users.disabled")}</Tag>}
            {self && <Tag>{t("share.you")}</Tag>}
          </span>
        }
      />
      <Facts>
        <Fact label={t("users.signIn")}>
          {u.has_password || u.providers.length ? (
            <span className="chips">
              {u.has_password && <Tag>{t("login.password")}</Tag>}
              {u.providers.map((p) => (
                <Tag key={p}>{p}</Tag>
              ))}
            </span>
          ) : (
            "–"
          )}
        </Fact>
        <Fact label={t("users.lastLogin")}>
          <Time iso={u.last_login_at} relative />
        </Fact>
      </Facts>
      {update.error && (
        <MessageBar intent="error">
          <MessageBarBody>{update.error.message}</MessageBarBody>
        </MessageBar>
      )}
      {!self && (
        <CardFooter className="wrap">
          <Checkbox
            label={t("users.admin")}
            checked={u.is_admin}
            disabled={update.isPending}
            onChange={(_, d) => update.mutate({ is_admin: d.checked === true })}
          />
          <Checkbox
            label={t("users.disabled")}
            checked={u.disabled}
            disabled={update.isPending}
            onChange={(_, d) => update.mutate({ disabled: d.checked === true })}
          />
          <Button size="small" icon={<Key20Regular />} onClick={onPassword}>
            {t("users.setPassword")}
          </Button>
          <ConfirmPopover
            title={t("users.removeTitle", { name: u.display_name })}
            body={t("users.removeBody")}
            onConfirm={onRemove}
          >
            <Button size="small" icon={<Delete20Regular />}>
              {t("common.remove")}
            </Button>
          </ConfirmPopover>
        </CardFooter>
      )}
    </Card>
  );
}

function InvitesList() {
  const { t } = useI18n();
  const client = useQueryClient();
  const roleName = useRoleName();
  const invites = useQuery({ queryKey: ["invites"], queryFn: api.invites });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const revoke = useMutation({
    mutationFn: api.deleteInvite,
    onSuccess: () => client.invalidateQueries({ queryKey: ["invites"] }),
  });
  const name = (id: string) => cameras.data?.find((c) => c.id === id)?.name ?? id;
  const open = invites.data?.filter((i) => i.state === "open") ?? [];
  return (
    <>
      <Subtitle2 as="h3" className="section-sub">
        {t("users.openInvites")}
      </Subtitle2>
      {open.length === 0 && <Empty>{t("users.noInvites")}</Empty>}
      <ul className="share-list">
        {open.map((inv) => (
          <li key={inv.id}>
            <span className="grow">
              <span className="chips">
                {inv.cameras.length ? (
                  inv.cameras.map((c) => (
                    <Tag key={c.camera_id}>
                      {name(c.camera_id)} ({roleName(c.role)})
                    </Tag>
                  ))
                ) : (
                  <Text>{t("users.accountOnly")}</Text>
                )}
                {inv.is_admin && <Tag kind="target">{t("users.admin")}</Tag>}
              </span>
              <Caption1 className="muted">
                {inv.note && <>“{inv.note}” – </>}
                {inv.created_by_name} – {t("share.expires")} <Time iso={inv.expires_at} />
              </Caption1>
            </span>
            <ConfirmPopover
              title={t("share.revokeTitle")}
              confirmLabel={t("share.revoke")}
              onConfirm={() => revoke.mutateAsync(inv.id)}
            >
              <Button
                appearance="subtle"
                icon={<Delete20Regular />}
                aria-label={t("share.revoke")}
              />
            </ConfirmPopover>
          </li>
        ))}
      </ul>
    </>
  );
}

function ErrorBar({ error }: { error: Error | null }) {
  if (!error) return null;
  return (
    <MessageBar intent="error">
      <MessageBarBody>{error.message}</MessageBarBody>
    </MessageBar>
  );
}

function AddUserDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [form, setForm] = useState({ email: "", name: "", password: "", is_admin: false });
  const add = useMutation({
    mutationFn: () => api.createUser({ ...form, password: form.password || null }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <DialogBody>
            <DialogTitle>{t("users.add")}</DialogTitle>
            <DialogContent className="form-stack">
              <Field label={t("login.email")} required>
                <Input
                  type="email"
                  value={form.email}
                  onChange={(_, d) => setForm({ ...form, email: d.value })}
                />
              </Field>
              <Field label={t("login.name")}>
                <Input value={form.name} onChange={(_, d) => setForm({ ...form, name: d.value })} />
              </Field>
              <Field label={t("login.password")} hint={t("users.passwordHint")}>
                <PasswordInput
                  autoComplete="new-password"
                  value={form.password}
                  onChange={(_, d) => setForm({ ...form, password: d.value })}
                />
              </Field>
              <Checkbox
                label={t("users.makeAdmin")}
                checked={form.is_admin}
                onChange={(_, d) => setForm({ ...form, is_admin: d.checked === true })}
              />
              <ErrorBar error={add.error} />
            </DialogContent>
            <DialogActions>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button
                type="submit"
                appearance="primary"
                disabled={
                  !form.email || (form.password !== "" && form.password.length < 8) || add.isPending
                }
              >
                {t("common.add")}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function InviteDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [admin, setAdmin] = useState(false);
  const create = useMutation({
    mutationFn: () => api.createInvite({ cameras: [], is_admin: admin, note }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["invites"] }),
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("users.invite")}</DialogTitle>
          <DialogContent className="form-stack">
            <Caption1 className="muted">{t("users.inviteHelp")}</Caption1>
            <Field label={t("users.note")}>
              <Input value={note} onChange={(_, d) => setNote(d.value)} />
            </Field>
            <Checkbox
              label={t("users.makeAdmin")}
              checked={admin}
              onChange={(_, d) => setAdmin(d.checked === true)}
            />
            <ErrorBar error={create.error} />
            {create.data && <InviteLink path={create.data.path} />}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t("common.close")}</Button>
            {!create.data && (
              <Button
                appearance="primary"
                icon={<Link20Regular />}
                disabled={create.isPending}
                onClick={() => create.mutate()}
              >
                {t("users.createLink")}
              </Button>
            )}
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function PasswordDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [password, setPassword] = useState("");
  const save = useMutation({
    mutationFn: () => api.setUserPassword(user.id, password),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <DialogBody>
            <DialogTitle>
              <DrawerTitle title={user.display_name} sub={t("account.newPassword")} />
            </DialogTitle>
            <DialogContent className="form-stack">
              <Caption1 className="muted">
                {t("users.passwordSignsOut", { name: user.display_name })}
              </Caption1>
              <Field label={t("account.newPassword")} hint={t("account.passwordHint")} required>
                <PasswordInput
                  autoComplete="new-password"
                  value={password}
                  onChange={(_, d) => setPassword(d.value)}
                />
              </Field>
              <ErrorBar error={save.error} />
            </DialogContent>
            <DialogActions>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button
                type="submit"
                appearance="primary"
                disabled={password.length < 8 || save.isPending}
              >
                {t("common.save")}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}
