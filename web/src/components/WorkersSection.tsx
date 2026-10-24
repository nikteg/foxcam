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
  Tooltip,
} from "@fluentui/react-components";
import { Add20Regular, Delete16Regular, Key16Regular, Tag16Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Worker, type WorkerToken } from "../api";
import { useI18n } from "../i18n";
import { AddWorkerDialog } from "./AddWorkerDialog";
import { splitTags } from "../workerSetup";
import { Dot, Empty, Tag, Time } from "./ui";

interface Row {
  id: string;
  worker?: Worker;
  token?: WorkerToken;
}

/** Connected workers and access tokens in one table (a token's name is its worker id). */
function mergeRows(workers: Worker[], tokens: WorkerToken[]): Row[] {
  const rows = new Map<string, Row>();
  for (const w of workers) rows.set(w.id, { id: w.id, worker: w });
  for (const tok of tokens) rows.set(tok.name, { ...rows.get(tok.name), id: tok.name, token: tok });
  const rank = (r: Row) => (r.worker?.online ? 0 : r.worker ? 1 : 2);
  return [...rows.values()].toSorted(
    (a, b) =>
      rank(a) - rank(b) ||
      (b.worker?.last_seen ?? "").localeCompare(a.worker?.last_seen ?? "") ||
      a.id.localeCompare(b.id),
  );
}

export function WorkersSection({ workers }: { workers: Worker[] }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const auth = useQuery({ queryKey: ["workers", "auth"], queryFn: api.workerAuth });
  const tokens = useQuery({ queryKey: ["workers", "tokens"], queryFn: api.workerTokens });
  const [adding, setAdding] = useState(false);
  const [revoking, setRevoking] = useState<WorkerToken | null>(null);
  const [tagging, setTagging] = useState<WorkerToken | null>(null);
  const remove = useMutation({
    mutationFn: api.removeWorker,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["queue"] });
      void client.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
  const rows = mergeRows(workers, tokens.data ?? []);

  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("queue.workers")}
        <Button
          size="small"
          appearance="primary"
          icon={<Add20Regular />}
          className="section-action"
          onClick={() => setAdding(true)}
        >
          {t("workers.add")}
        </Button>
      </Subtitle2>
      {auth.data && (
        <MessageBar
          intent={auth.data.mode === "open" ? "warning" : auth.data.tokens ? "success" : "info"}
          className="block"
        >
          <MessageBarBody>
            {auth.data.mode === "open"
              ? t("workers.modeOpen")
              : auth.data.tokens || auth.data.shared_token
                ? t("workers.modeLocked")
                : t("workers.modeLockedEmpty")}
            {auth.data.shared_token && ` ${t("workers.sharedToken")}`}
          </MessageBarBody>
        </MessageBar>
      )}
      {rows.length > 0 && (
        <Table className="table responsive" aria-label={t("queue.workers")}>
          <TableHeader>
            <TableRow>
              <TableHeaderCell>{t("col.worker")}</TableHeaderCell>
              <TableHeaderCell>{t("queue.info")}</TableHeaderCell>
              <TableHeaderCell>{t("queue.lastSeen")}</TableHeaderCell>
              <TableHeaderCell>{t("queue.currentJob")}</TableHeaderCell>
              <TableHeaderCell>{t("queue.doneCount")}</TableHeaderCell>
              <TableHeaderCell>{t("queue.failedCount")}</TableHeaderCell>
              <TableHeaderCell aria-label={t("col.actions")} className="col-actions wide" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ id, worker: w, token: tok }) => {
              const tags = [...new Set([...(tok?.tags ?? []), ...(w?.tags ?? [])])];
              return (
                <TableRow key={id}>
                  <TableCell data-label={t("col.worker")}>
                    <span className="row">
                      <Dot ok={!!w?.online} />
                      <Text weight="semibold" wrap={false}>
                        {id}
                      </Text>
                      {tok && (
                        <Tag kind="ok" title={t("workers.hasToken")}>
                          {t("workers.tokenBadge")}
                        </Tag>
                      )}
                    </span>
                  </TableCell>
                  <TableCell data-label={t("queue.info")} className="full mono">
                    {/* One chip per key so values wrap as units, never mid-word. */}
                    <span className="chips">
                      {tags.map((tag) => (
                        <Tag key={`tag-${tag}`}>{tag}</Tag>
                      ))}
                      {!w && tok?.detector && (
                        <span>
                          <span className="muted">detector=</span>
                          {tok.detector}
                        </span>
                      )}
                      {Object.entries(w?.info ?? {}).map(([k, v]) => (
                        <span key={k}>
                          <span className="muted">{k}=</span>
                          {String(v)}
                        </span>
                      ))}
                    </span>
                  </TableCell>
                  <TableCell data-label={t("queue.lastSeen")}>
                    {w ? (
                      <Time iso={w.last_seen} relative />
                    ) : (
                      <span className="muted">{t("workers.neverConnected")}</span>
                    )}
                  </TableCell>
                  <TableCell data-label={t("queue.currentJob")}>
                    {w?.current_job_id ?? "–"}
                  </TableCell>
                  <TableCell data-label={t("queue.doneCount")}>{w?.jobs_done ?? 0}</TableCell>
                  <TableCell data-label={t("queue.failedCount")}>{w?.jobs_failed ?? 0}</TableCell>
                  <TableCell className="full">
                    <span className="row nowrap-actions">
                      {tok && (
                        <Button
                          size="small"
                          icon={<Tag16Regular />}
                          onClick={() => setTagging(tok)}
                        >
                          {t("workers.editTags")}
                        </Button>
                      )}
                      {tok && (
                        <Button
                          size="small"
                          icon={<Key16Regular />}
                          onClick={() => setRevoking(tok)}
                        >
                          {t("workers.revoke")}
                        </Button>
                      )}
                      {w && !w.online && (
                        <Tooltip content={t("queue.removeWorkerHint")} relationship="description">
                          <Button
                            size="small"
                            icon={<Delete16Regular />}
                            disabled={remove.isPending}
                            onClick={() => remove.mutate(w.id)}
                          >
                            {t("common.remove")}
                          </Button>
                        </Tooltip>
                      )}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {rows.length === 0 && <Empty>{t("queue.noWorkers")}</Empty>}
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
      void client.invalidateQueries({ queryKey: ["queue"] });
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
