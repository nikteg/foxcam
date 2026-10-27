// Said above every page once the server serves a newer build than this page runs: the old
// app may ask for API endpoints that changed (newVersion.ts).
import { Button, MessageBar, MessageBarActions, MessageBarBody } from "@fluentui/react-components";
import { Dismiss20Regular } from "@fluentui/react-icons";
import { useState } from "react";

import { useI18n } from "../i18n";
import { useNewerBuild } from "../newVersion";

export function NewVersionBanner() {
  const { t } = useI18n();
  const newer = useNewerBuild();
  const [dismissed, setDismissed] = useState(false);
  if (!newer || dismissed) return null;
  return (
    <MessageBar intent="info" className="block">
      <MessageBarBody>{t("app.newVersion")}</MessageBarBody>
      <MessageBarActions
        containerAction={
          <Button
            appearance="transparent"
            icon={<Dismiss20Regular />}
            aria-label={t("common.close")}
            onClick={() => setDismissed(true)}
          />
        }
      >
        <Button size="small" onClick={() => window.location.reload()}>
          {t("app.reload")}
        </Button>
      </MessageBarActions>
    </MessageBar>
  );
}
