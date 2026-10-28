import { Button, Field, MessageBar, MessageBarBody, Text } from "@fluentui/react-components";
import { Delete16Regular, Key16Regular, Tag16Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Worker, type WorkerToken } from "../api";
import { ConfirmPopover } from "./ConfirmPopover";
import { DataTable, type Column } from "./DataTable";
import { useI18n, type I18n } from "../i18n";
import { TagsPicker } from "./LabelPicker";
import { Dot, DrawerTitle, Empty, Tag, Time } from "./ui";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "./Drawer";
import { useToast } from "./Toasts";

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

/** What the row buttons do. */
interface RowActions {
  tag: (token: WorkerToken) => void;
  revoke: (token: WorkerToken) => Promise<unknown>;
  remove: (workerId: string) => Promise<unknown>;
}

const workerColumns = (t: I18n["t"], on: RowActions): Column<Row>[] => [
  {
    id: "worker",
    header: t("col.worker"),
    width: 200,
    cell: ({ id, worker: w, token: tok }) => (
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
    ),
  },
  {
    id: "info",
    header: t("queue.info"),
    width: 320,
    cell: ({ worker: w, token: tok }) => (
      // One chip per key so values wrap as units, never mid-word.
      <span className="chips mono">
        {[...new Set([...(tok?.tags ?? []), ...(w?.tags ?? [])])].map((tag) => (
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
    ),
  },
  {
    id: "seen",
    header: t("queue.lastSeen"),
    width: 140,
    cell: ({ worker: w }) =>
      w ? (
        <Time iso={w.last_seen} relative />
      ) : (
        <span className="muted">{t("workers.neverConnected")}</span>
      ),
  },
  {
    id: "job",
    header: t("queue.currentJob"),
    width: 100,
    cell: ({ worker: w }) => w?.current_job_id ?? "–",
  },
  {
    id: "done",
    header: t("queue.doneCount"),
    width: 80,
    cell: ({ worker: w }) => w?.jobs_done ?? 0,
  },
  {
    id: "failed",
    header: t("queue.failedCount"),
    width: 80,
    cell: ({ worker: w }) => w?.jobs_failed ?? 0,
  },
  {
    id: "actions",
    header: "",
    width: 300,
    phone: "nolabel",
    cell: ({ worker: w, token: tok }) => (
      <span className="row nowrap-actions">
        {tok && (
          <Button size="small" icon={<Tag16Regular />} onClick={() => on.tag(tok)}>
            {t("workers.editTags")}
          </Button>
        )}
        {tok && (
          <ConfirmPopover
            title={t("workers.revokeTitle", { name: tok.name })}
            body={t("workers.revokeBody")}
            confirmLabel={t("workers.revoke")}
            onConfirm={() => on.revoke(tok)}
          >
            <Button size="small" icon={<Key16Regular />}>
              {t("workers.revoke")}
            </Button>
          </ConfirmPopover>
        )}
        {w && !w.online && (
          <ConfirmPopover
            title={t("workers.removeTitle", { name: w.id })}
            body={t("queue.removeWorkerHint")}
            onConfirm={() => on.remove(w.id)}
          >
            <Button size="small" icon={<Delete16Regular />}>
              {t("common.remove")}
            </Button>
          </ConfirmPopover>
        )}
      </span>
    ),
  },
];

export function WorkersSection({ workers }: { workers: Worker[] }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const auth = useQuery({ queryKey: ["workers", "auth"], queryFn: api.workerAuth });
  const tokens = useQuery({ queryKey: ["workers", "tokens"], queryFn: api.workerTokens });
  const revoke = (tok: WorkerToken) =>
    api.revokeWorkerToken(tok.id).then(() => {
      void client.invalidateQueries({ queryKey: ["workers"] });
      void client.invalidateQueries({ queryKey: ["queue"] });
    });
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
        <DataTable
          ariaLabel={t("queue.workers")}
          items={rows}
          getRowId={(r) => r.id}
          columns={workerColumns(t, { tag: setTagging, revoke, remove: remove.mutateAsync })}
        />
      )}
      {rows.length === 0 && <Empty>{t("queue.noWorkers")}</Empty>}
      {tagging && <TagsDialog token={tagging} onClose={() => setTagging(null)} />}
    </>
  );
}

function TagsDialog({ token, onClose }: { token: WorkerToken; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [tags, setTags] = useState<string[]>(token.tags);
  const toast = useToast();
  const save = useMutation({
    mutationFn: () => api.setTokenTags(token.id, tags),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workers"] });
      void client.invalidateQueries({ queryKey: ["profiles"] });
      toast.success(t("common.saved"));
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
              <DrawerTitle title={token.name} sub={t("workers.tags")} />
            </DialogTitle>
            <DialogContent className="form-stack">
              <Field label={t("workers.tags")} hint={t("workers.tagsHint")}>
                <TagsPicker value={tags} onChange={setTags} ariaLabel={t("workers.tags")} />
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
