import { Button, Caption1, MessageBar, MessageBarBody } from "@fluentui/react-components";
import { AlertOff20Regular, AlertOn20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { useI18n } from "../i18n";
import { currentEndpointId, deviceName, PushDenied, pushSupport, subscribe } from "../push";

/** This device's push subscription id (null: none). */
export function useThisDevice(): string | null {
  return useQuery({ queryKey: ["push-endpoint"], queryFn: currentEndpointId }).data ?? null;
}

/** Turning the user's own notifications on or off for the device the page is open on. */
export function usePushToggle(devices: { id: number; endpoint_id: string }[], pushKey: string) {
  const client = useQueryClient();
  const thisDevice = useThisDevice();
  const mine = devices.find((d) => d.endpoint_id === thisDevice);
  const change = useMutation({
    mutationFn: async () => {
      if (mine) return api.removePushDevice(mine.id);
      const sub = await subscribe(pushKey);
      return api.addPushDevice({ ...sub, name: deviceName() });
    },
    onSettled: () =>
      Promise.all(
        [["push"], ["push-endpoint"]].map((queryKey) => client.invalidateQueries({ queryKey })),
      ),
  });
  return { support: pushSupport(), on: !!mine, change };
}

export type PushToggle = ReturnType<typeof usePushToggle>;

/** "Turn on here" / "Turn off here"; nothing where this device can't get push. */
export function PushToggleButton({ push }: { push: PushToggle }) {
  const { t } = useI18n();
  if (push.support !== "ok") return null;
  return (
    <Button
      size="small"
      icon={push.on ? <AlertOff20Regular /> : <AlertOn20Regular />}
      disabled={push.change.isPending}
      onClick={() => push.change.mutate()}
    >
      {push.on ? t("notify.pushOff") : t("notify.pushOn")}
    </Button>
  );
}

/** Why this device can't get push (add to Home Screen on iOS), or what went wrong. */
export function PushToggleMessage({ push }: { push: PushToggle }) {
  const { t } = useI18n();
  if (push.support !== "ok") {
    return (
      <Caption1 className="muted">
        {push.support === "ios-browser" ? t("notify.pushIosHint") : t("notify.pushUnsupported")}
      </Caption1>
    );
  }
  if (!push.change.error) return null;
  return (
    <MessageBar intent="error">
      <MessageBarBody>
        {push.change.error instanceof PushDenied
          ? t("notify.pushDenied")
          : push.change.error.message}
      </MessageBarBody>
    </MessageBar>
  );
}
