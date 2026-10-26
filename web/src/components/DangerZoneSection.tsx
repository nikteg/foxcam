// Settings → Danger zone (admins): delete one kind of data and keep the rest, like
// `foxcam reset` on the server. Each action asks first, at its button.
import { Button, Field, MessageBar, MessageBarBody, Subtitle2 } from "@fluentui/react-components";
import { ArrowDownload20Regular, Delete20Regular } from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type DangerScope } from "../api";
import { useI18n } from "../i18n";
import { ConfirmPopover } from "./ConfirmPopover";
import { InfoBanner } from "./ui";

/** In the order of how much they take away. ``marks``: correct/wrong marks go too. */
const ACTIONS: { scope: DangerScope; marks?: boolean }[] = [
  { scope: "queue" },
  { scope: "detections", marks: true },
  { scope: "footage" },
  { scope: "recordings", marks: true },
  { scope: "workers" },
  { scope: "profiles" },
  { scope: "cameras" },
];

function DangerAction({ scope, marks }: { scope: DangerScope; marks?: boolean }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const reset = useMutation({
    mutationFn: () => api.dangerReset(scope),
    onSuccess: () => void client.invalidateQueries(),
  });
  return (
    <>
      <Field label={t(`danger.${scope}`)} hint={t(`danger.${scope}Hint`)}>
        <div className="row wrap">
          <ConfirmPopover
            title={t(`danger.${scope}Confirm`)}
            body={t("danger.confirmBody")}
            confirmLabel={t(`danger.${scope}Button`)}
            typeToConfirm={t(`danger.${scope}Word`)}
            align="start"
            onConfirm={() => reset.mutateAsync()}
          >
            <Button className="danger" icon={<Delete20Regular />}>
              {t(`danger.${scope}Button`)}
            </Button>
          </ConfirmPopover>
          {marks && (
            // The marks are training data: they can be kept as a file first.
            <Button
              as="a"
              size="small"
              appearance="subtle"
              icon={<ArrowDownload20Regular />}
              href="/api/detections/feedback-export"
              download="foxcam-marks.json"
            >
              {t("danger.downloadMarks")}
            </Button>
          )}
        </div>
      </Field>
      {reset.isSuccess && (
        <MessageBar intent="success">
          <MessageBarBody>{t("danger.done")}</MessageBarBody>
        </MessageBar>
      )}
    </>
  );
}

export function DangerZoneSection() {
  const { t } = useI18n();
  return (
    <section className="form-stack settings-section">
      <InfoBanner id="danger-zone">{t("danger.intro")}</InfoBanner>
      <Subtitle2 as="h2" className="panel-heading">
        {t("danger.title")}
      </Subtitle2>
      {ACTIONS.map((a) => (
        <DangerAction key={a.scope} {...a} />
      ))}
    </section>
  );
}
