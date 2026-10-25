import {
  Body1,
  Button,
  Caption1,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Select,
  Subtitle2,
  Text,
} from "@fluentui/react-components";
import {
  Copy20Regular,
  Delete20Regular,
  Link20Regular,
  PersonAdd20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError, api, type Camera, type CameraRole } from "../api";
import { useMe } from "../auth";
import { useI18n } from "../i18n";
import { ConfirmPopover } from "./ConfirmPopover";
import { Time } from "./ui";
import { DialogActions, DialogContent } from "./Drawer";

export const ROLES: CameraRole[] = ["viewer", "editor", "owner"];

export function useRoleName(): (role: CameraRole) => string {
  const { t } = useI18n();
  return (role) => t(`share.role.${role}`);
}

function RoleSelect({
  value,
  onChange,
  disabled,
  label,
}: {
  value: CameraRole;
  onChange: (role: CameraRole) => void;
  disabled?: boolean;
  label: string;
}) {
  const roleName = useRoleName();
  return (
    <Select
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(_, d) => onChange(d.value as CameraRole)}
    >
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {roleName(r)}
        </option>
      ))}
    </Select>
  );
}

/** Copyable full URL of an invite link. */
export function InviteLink({ path }: { path: string }) {
  const { t } = useI18n();
  const url = `${window.location.origin}${path}`;
  const [copied, setCopied] = useState(false);
  return (
    <Field label={t("share.linkLabel")} hint={t("share.linkHint")}>
      <div className="row">
        <Input className="grow mono" value={url} readOnly onFocus={(e) => e.target.select()} />
        <Button
          icon={<Copy20Regular />}
          onClick={() => void navigator.clipboard?.writeText(url).then(() => setCopied(true))}
        >
          {copied ? t("share.copied") : t("share.copy")}
        </Button>
      </div>
    </Field>
  );
}

/** Who can see a camera: people with access, invites, and adding more (owners). */
/** Who can see this camera (camera Settings → Sharing). */
export function SharePanel({
  camera,
  tabs,
  onClose,
}: {
  camera: Camera;
  tabs: ReactNode;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const roleName = useRoleName();
  const access = useQuery({
    queryKey: ["camera-access", camera.id],
    queryFn: () => api.cameraAccess(camera.id),
  });
  const directory = useQuery({ queryKey: ["directory"], queryFn: api.directory });
  const refresh = () => client.invalidateQueries({ queryKey: ["camera-access", camera.id] });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<CameraRole>("viewer");
  const [link, setLink] = useState<string | null>(null);

  const share = useMutation({
    mutationFn: () => api.share(camera.id, email.trim(), role),
    onSuccess: () => {
      setEmail("");
      void refresh();
    },
  });
  const setMemberRole = useMutation({
    mutationFn: ({ userId, r }: { userId: number; r: CameraRole }) =>
      api.setShareRole(camera.id, userId, r),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (userId: number) => api.unshare(camera.id, userId),
    onSuccess: refresh,
  });
  const invite = useMutation({
    mutationFn: () => api.createInvite({ cameras: [{ camera_id: camera.id, role }] }),
    onSuccess: (r) => {
      setLink(r.path);
      void refresh();
    },
  });
  const revoke = useMutation({ mutationFn: api.deleteInvite, onSuccess: refresh });
  const noAccount = share.error instanceof ApiError && share.error.status === 404;
  const error = (noAccount ? null : share.error) ?? setMemberRole.error ?? remove.error;

  return (
    <>
      <DialogContent className="form-stack">
        {tabs}
        <Body1 className="muted">{t("share.intro")}</Body1>

        <form
          className="share-add"
          onSubmit={(e) => {
            e.preventDefault();
            setLink(null);
            share.mutate();
          }}
        >
          <Field label={t("share.email")} className="grow">
            <Input
              type="email"
              list="share-directory"
              value={email}
              onChange={(_, d) => setEmail(d.value)}
            />
          </Field>
          <datalist id="share-directory">
            {directory.data?.map((u) => (
              <option key={u.id} value={u.email}>
                {u.display_name}
              </option>
            ))}
          </datalist>
          <Field label={t("share.roleLabel")}>
            <RoleSelect value={role} onChange={setRole} label={t("share.roleLabel")} />
          </Field>
          <Button
            type="submit"
            appearance="primary"
            icon={<PersonAdd20Regular />}
            disabled={!email.includes("@") || share.isPending}
          >
            {t("share.add")}
          </Button>
        </form>
        <Caption1 className="muted">{t(`share.roleHelp.${role}`)}</Caption1>
        {noAccount && (
          <MessageBar>
            <MessageBarBody>{t("share.noAccount", { email })}</MessageBarBody>
          </MessageBar>
        )}
        {error && (
          <MessageBar intent="error">
            <MessageBarBody>{error.message}</MessageBarBody>
          </MessageBar>
        )}

        <div className="row wrap">
          <Button
            icon={<Link20Regular />}
            disabled={invite.isPending}
            onClick={() => invite.mutate()}
          >
            {t("share.createLink", { role: roleName(role) })}
          </Button>
        </div>
        {invite.error && (
          <MessageBar intent="error">
            <MessageBarBody>{invite.error.message}</MessageBarBody>
          </MessageBar>
        )}
        {link && <InviteLink path={link} />}

        <Subtitle2 as="h3">{t("share.people")}</Subtitle2>
        {access.data?.members.length === 0 && (
          <Caption1 className="muted">{t("share.nobody")}</Caption1>
        )}
        <ul className="share-list">
          {access.data?.members.map((m) => (
            <li key={m.user_id}>
              <span className="share-who">
                <Text weight="semibold">
                  {m.display_name}
                  {m.user_id === me?.user?.id && (
                    <Caption1 className="muted"> ({t("share.you")})</Caption1>
                  )}
                </Text>
                {m.name && <Caption1 className="muted">{m.email}</Caption1>}
              </span>
              <RoleSelect
                value={m.role}
                label={t("share.roleFor", { name: m.display_name })}
                disabled={setMemberRole.isPending}
                onChange={(r) => setMemberRole.mutate({ userId: m.user_id, r })}
              />
              <ConfirmPopover
                title={t("share.removeFor", { name: m.display_name })}
                onConfirm={() => remove.mutateAsync(m.user_id)}
              >
                <Button
                  appearance="subtle"
                  icon={<Delete20Regular />}
                  aria-label={t("share.removeFor", { name: m.display_name })}
                />
              </ConfirmPopover>
            </li>
          ))}
        </ul>
        {me?.is_admin && <Caption1 className="muted">{t("share.adminsSeeAll")}</Caption1>}

        {(access.data?.invites.length ?? 0) > 0 && (
          <>
            <Subtitle2 as="h3">{t("share.openInvites")}</Subtitle2>
            <ul className="share-list">
              {access.data?.invites.map((inv) => (
                <li key={inv.id}>
                  <span className="share-who">
                    {roleName(inv.cameras.find((c) => c.camera_id === camera.id)?.role ?? "viewer")}{" "}
                    <Caption1 className="muted">
                      {t("share.expires")} <Time iso={inv.expires_at} />
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
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.close")}</Button>
      </DialogActions>
    </>
  );
}
