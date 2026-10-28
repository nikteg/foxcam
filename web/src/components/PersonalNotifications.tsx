import { Button, Subtitle2, Switch, Text } from "@fluentui/react-components";
import { Delete20Regular, Save20Regular, Send20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type PushDevice, type PushOverview } from "../api";
import { useI18n, type I18n } from "../i18n";
import { ConfirmPopover } from "./ConfirmPopover";
import { DataTable, type Column } from "./DataTable";
import { draftFromRules, NotificationRulesFields, rulesFromDraft } from "./NotificationRulesFields";
import { PushToggleButton, PushToggleMessage, usePushToggle, useThisDevice } from "./PushToggle";
import { useToast } from "./Toasts";
import { InfoBanner, Tag, Time } from "./ui";

/** The browser's time zone: the user's quiet hours and message times are in it. */
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

/**
 * The signed-in user's own notifications (Account page): turn them on for this device, the
 * devices that have them, and which events to notify about. Only cameras they can see.
 */
export function PersonalNotifications() {
  const { t } = useI18n();
  const push = useQuery({ queryKey: ["push"], queryFn: api.push });
  return (
    <>
      <Subtitle2 as="h2" className="section">
        {t("account.notifications")}
      </Subtitle2>
      <InfoBanner id="personal-notifications">{t("push.intro")}</InfoBanner>
      {push.data && <PushSettings data={push.data} />}
    </>
  );
}

const deviceColumns = (
  t: I18n["t"],
  thisDevice: string | null,
  onRemove: (id: number) => Promise<unknown>,
): Column<PushDevice>[] => [
  {
    id: "name",
    header: t("push.device"),
    width: 180,
    cell: (d) => (
      <span className="row wrap">
        <Text weight="semibold">{d.name ?? "?"}</Text>
        {d.endpoint_id === thisDevice && <Tag kind="brand">{t("push.thisDevice")}</Tag>}
      </span>
    ),
  },
  {
    id: "added",
    header: t("push.added"),
    width: 140,
    cell: (d) => <Time iso={d.created_at} relative />,
  },
  {
    id: "sent",
    header: t("push.lastSent"),
    width: 140,
    cell: (d) =>
      d.last_sent_at ? (
        <Time iso={d.last_sent_at} relative />
      ) : (
        <span className="muted">{t("apiKeys.never")}</span>
      ),
  },
  {
    id: "actions",
    header: "",
    width: 120,
    phone: "nolabel",
    cell: (d) => (
      <ConfirmPopover
        title={t("push.removeTitle", { name: d.name ?? "?" })}
        body={t("push.removeBody")}
        onConfirm={() => onRemove(d.id)}
      >
        <Button size="small" icon={<Delete20Regular />}>
          {t("common.remove")}
        </Button>
      </ConfirmPopover>
    ),
  },
];

function PushSettings({ data }: { data: PushOverview }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const push = usePushToggle(data.devices, data.key);
  const thisDevice = useThisDevice();
  const toast = useToast();
  const test = useMutation({
    mutationFn: api.testPush,
    onSuccess: (r) => (r.ok ? toast.success(t("notify.testOk")) : toast.error(r.detail)),
    onError: (e) => toast.error(e),
  });
  const remove = useMutation({
    mutationFn: api.removePushDevice,
    onSettled: () =>
      Promise.all(
        [["push"], ["push-endpoint"]].map((queryKey) => client.invalidateQueries({ queryKey })),
      ),
  });

  return (
    <div className="form-stack">
      <PushToggleMessage push={push} />
      <div className="row wrap">
        <PushToggleButton push={push} />
        {data.devices.length > 0 && (
          <Button
            size="small"
            icon={<Send20Regular />}
            disabled={test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? t("notify.testing") : t("notify.test")}
          </Button>
        )}
      </div>
      {data.devices.length > 0 && (
        <DataTable
          ariaLabel={t("push.devices")}
          items={data.devices}
          getRowId={(d) => d.id}
          columns={deviceColumns(t, thisDevice, remove.mutateAsync)}
        />
      )}
      <RulesForm data={data} />
    </div>
  );
}

/** Which events to notify about; saved with this browser's time zone. */
function RulesForm({ data }: { data: PushOverview }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(data.rules.enabled);
  const [rules, setRules] = useState(() => draftFromRules(data.rules));
  const toast = useToast();
  const save = useMutation({
    mutationFn: () => api.savePushRules({ enabled, ...rulesFromDraft(rules), timezone: ZONE }),
    onSuccess: () => {
      toast.success(t("common.saved"));
      return client.invalidateQueries({ queryKey: ["push"] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <h2 className="panel-heading">{t("push.rules")}</h2>
      <Switch
        label={t("push.enabled")}
        checked={enabled}
        onChange={(_, d) => setEnabled(d.checked)}
      />
      <NotificationRulesFields
        value={rules}
        onChange={setRules}
        quietHint={t("push.quietHint", { zone: ZONE })}
      />
      <div>
        <Button
          type="submit"
          appearance="primary"
          icon={<Save20Regular />}
          disabled={rules.when.length === 0 || save.isPending}
        >
          {t("common.save")}
        </Button>
      </div>
    </form>
  );
}
