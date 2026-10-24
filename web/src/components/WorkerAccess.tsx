import {
  Button,
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
  Subtitle2,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from "@fluentui/react-components";
import { Add20Regular, Delete16Regular, Tag16Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type WorkerToken } from "../api";
import { useI18n } from "../i18n";
import { AddWorkerDialog } from "./AddWorkerDialog";
import { splitTags } from "../workerSetup";
import { Dot, Tag, Time } from "./ui";

/** Worker tokens: who may run jobs, and the button to set up a new worker machine. */
export function WorkerAccess({ onlineIds }: { onlineIds: Set<string> }) {
  const { t } = useI18n();
  const auth = useQuery({ queryKey: ["workers", "auth"], queryFn: api.workerAuth });
  const tokens = useQuery({ queryKey: ["workers", "tokens"], queryFn: api.workerTokens });
  const [adding, setAdding] = useState(false);
  const [revoking, setRevoking] = useState<WorkerToken | null>(null);
  const [tagging, setTagging] = useState<WorkerToken | null>(null);

  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("workers.access")}
        <Button
          size="small"
          appearance="primary"
          icon={<Add20Regular />}
          onClick={() => setAdding(true)}
        >
          {t("workers.add")}
        </Button>
      </Subtitle2>
      {auth.data && (
        <MessageBar intent={auth.data.mode === "open" ? "warning" : "success"} className="block">
          <MessageBarBody>
            {auth.data.mode === "open" ? t("workers.modeOpen") : t("workers.modeLocked")}
            {auth.data.shared_token && ` ${t("workers.sharedToken")}`}
          </MessageBarBody>
        </MessageBar>
      )}
      {tokens.data && tokens.data.length > 0 && (
        <Table className="table responsive" aria-label={t("workers.access")}>
          <TableHeader>
            <TableRow>
              <TableHeaderCell>{t("workers.name")}</TableHeaderCell>
              <TableHeaderCell>{t("workers.detector")}</TableHeaderCell>
              <TableHeaderCell>{t("workers.tags")}</TableHeaderCell>
              <TableHeaderCell>{t("workers.created")}</TableHeaderCell>
              <TableHeaderCell>{t("workers.lastUsed")}</TableHeaderCell>
              <TableHeaderCell aria-label={t("col.actions")} className="col-actions" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens.data.map((tok) => (
              <TableRow key={tok.id}>
                <TableCell data-label={t("workers.name")}>
                  <span className="row">
                    <Dot ok={onlineIds.has(tok.name)} />
                    <Text weight="semibold" wrap={false}>
                      {tok.name}
                    </Text>
                  </span>
                </TableCell>
                <TableCell data-label={t("workers.detector")}>{tok.detector ?? "–"}</TableCell>
                <TableCell data-label={t("workers.tags")}>
                  {tok.tags.length ? (
                    <span className="row wrap">
                      {tok.tags.map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </span>
                  ) : (
                    "–"
                  )}
                </TableCell>
                <TableCell data-label={t("workers.created")}>
                  <Time iso={tok.created_at} />
                </TableCell>
                <TableCell data-label={t("workers.lastUsed")}>
                  {tok.last_used_at ? <Time iso={tok.last_used_at} relative /> : t("workers.never")}
                </TableCell>
                <TableCell className="full">
                  <span className="row">
                    <Button size="small" icon={<Tag16Regular />} onClick={() => setTagging(tok)}>
                      {t("workers.editTags")}
                    </Button>
                    <Button
                      size="small"
                      icon={<Delete16Regular />}
                      onClick={() => setRevoking(tok)}
                    >
                      {t("workers.revoke")}
                    </Button>
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <AddWorkerDialog open={adding} onClose={() => setAdding(false)} />
      {revoking && <RevokeDialog token={revoking} onClose={() => setRevoking(null)} />}
      {tagging && <TagsDialog token={tagging} onClose={() => setTagging(null)} />}
    </>
  );
}

function TagsDialog({ token, onClose }: { token: WorkerToken; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [tags, setTags] = useState(token.tags.join(", "));
  const save = useMutation({
    mutationFn: () => api.setTokenTags(token.id, splitTags(tags)),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workers"] });
      void client.invalidateQueries({ queryKey: ["profiles"] });
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
            <DialogTitle>{t("workers.tagsTitle", { name: token.name })}</DialogTitle>
            <DialogContent className="form-stack">
              <Field label={t("workers.tags")} hint={t("workers.tagsHint")}>
                <Input value={tags} onChange={(_, d) => setTags(d.value)} />
              </Field>
              {save.error && (
                <MessageBar intent="error">
                  <MessageBarBody>{save.error.message}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button type="submit" appearance="primary" disabled={save.isPending}>
                {t("common.save")}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function RevokeDialog({ token, onClose }: { token: WorkerToken; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.revokeWorkerToken(token.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workers"] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{t("workers.revokeTitle", { name: token.name })}</DialogTitle>
          <DialogContent>{t("workers.revokeBody")}</DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button
              appearance="primary"
              icon={<Delete16Regular />}
              disabled={revoke.isPending}
              onClick={() => revoke.mutate()}
            >
              {t("workers.revoke")}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
