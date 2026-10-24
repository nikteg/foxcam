import {
  Badge,
  Button,
  Card,
  CardHeader,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
} from "@fluentui/react-components";
import { CloudArrowUp20Regular, PlugConnected20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useI18n } from "../i18n";
import { QueryState } from "./ui";

/** S3-compatible object storage: what is uploaded, progress and a connection test. */
export function StorageCard() {
  const { t, rich } = useI18n();
  const storage = useQuery({
    queryKey: ["storage"],
    queryFn: api.storage,
    refetchInterval: 15_000,
  });
  const test = useMutation({ mutationFn: api.testStorage });
  const s = storage.data;

  return (
    <Card className="storage-card">
      <CardHeader
        image={<CloudArrowUp20Regular />}
        header={<Text weight="semibold">{t("storage.s3")}</Text>}
        action={
          s && (
            <Badge appearance="tint" color={s.enabled ? "success" : "informative"}>
              {s.enabled ? t("storage.enabled") : t("storage.disabled")}
            </Badge>
          )
        }
      />
      <QueryState error={storage.error} loading={storage.isPending} />
      {s && !s.enabled && (
        <Text className="muted">
          {rich("storage.disabledHint", {
            section: <code>storage.s3</code>,
            file: <code>config.yaml</code>,
          })}
        </Text>
      )}
      {s?.enabled && (
        <>
          {!s.boto3 && (
            <MessageBar intent="error">
              <MessageBarBody>
                {rich("storage.noBoto3", { cmd: <code>uv sync --all-packages --extra s3</code> })}
              </MessageBarBody>
            </MessageBar>
          )}
          <dl className="facts">
            <dt>{t("storage.bucket")}</dt>
            <dd className="mono">
              {s.bucket}
              {s.prefix && `/${s.prefix.replace(/^\/+/, "")}`}
            </dd>
            <dt>{t("storage.endpoint")}</dt>
            <dd className="mono">{s.endpoint_url ?? "AWS S3"}</dd>
            <dt>{t("storage.clips")}</dt>
            <dd>
              {s.clips
                ? t("storage.progress", {
                    done: s.clips_uploaded ?? 0,
                    pending: s.clips_pending ?? 0,
                  })
                : t("storage.off")}
              {s.clips && s.delete_local_clips && (
                <Text className="muted"> – {t("storage.deleteLocal")}</Text>
              )}
            </dd>
            <dt>{t("storage.recordings")}</dt>
            <dd>
              {s.recordings
                ? t("storage.progress", {
                    done: s.recordings_uploaded ?? 0,
                    pending: s.recordings_pending ?? 0,
                  })
                : t("storage.off")}
              {s.recordings && s.recordings_days > 0 && (
                <Text className="muted"> – {t("storage.keepDays", { n: s.recordings_days })}</Text>
              )}
            </dd>
          </dl>
          {s.last_error && (
            <MessageBar intent="warning">
              <MessageBarBody>
                <MessageBarTitle>{t("storage.lastError")}</MessageBarTitle>
                {s.last_error}
              </MessageBarBody>
            </MessageBar>
          )}
          {test.data && (
            <MessageBar intent={test.data.ok ? "success" : "error"}>
              <MessageBarBody>
                <MessageBarTitle>
                  {test.data.ok ? t("storage.testOk") : t("storage.testFailed")}
                </MessageBarTitle>
                {test.data.detail}
              </MessageBarBody>
            </MessageBar>
          )}
          {test.error && (
            <MessageBar intent="error">
              <MessageBarBody>{test.error.message}</MessageBarBody>
            </MessageBar>
          )}
          <div>
            <Button
              icon={<PlugConnected20Regular />}
              disabled={test.isPending}
              onClick={() => test.mutate()}
            >
              {test.isPending ? t("storage.testing") : t("storage.test")}
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}
