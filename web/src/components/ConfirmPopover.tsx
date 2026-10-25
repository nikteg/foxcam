// "Are you sure?" for removing things: a small popover at the button, not a drawer.
import {
  Button,
  MessageBar,
  MessageBarBody,
  Popover,
  PopoverSurface,
  PopoverTrigger,
  Text,
} from "@fluentui/react-components";
import { Delete20Regular } from "@fluentui/react-icons";
import { useState, type ReactElement } from "react";

import { useI18n } from "../i18n";

/** Wraps the button that asks; ``onConfirm`` runs on the red button and the popover closes
 * when it succeeds (an error is shown in it). With ``open`` / ``onOpenChange`` the caller
 * decides when it shows (e.g. also when a drawer is closed another way). */
export function ConfirmPopover({
  title,
  body,
  confirmLabel,
  icon,
  onConfirm,
  align = "end",
  open: openProp,
  onOpenChange,
  children,
}: {
  title: string;
  body?: string;
  /** Default: Remove. */
  confirmLabel?: string;
  /** Of the red button. Default: a bin. */
  icon?: ReactElement;
  onConfirm: () => Promise<unknown>;
  /** Which edge of the button the popover lines up with. */
  align?: "start" | "end";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** The trigger button. */
  children: ReactElement;
}) {
  const { t } = useI18n();
  const [ownOpen, setOwnOpen] = useState(false);
  const open = openProp ?? ownOpen;
  const setOpen = (o: boolean) => {
    setOwnOpen(o);
    onOpenChange?.(o);
  };
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Popover
      open={open}
      onOpenChange={(_, d) => {
        setOpen(d.open);
        setError(null);
      }}
      positioning={align === "start" ? "above-start" : "above-end"}
      withArrow
      trapFocus
    >
      <PopoverTrigger disableButtonEnhancement>{children}</PopoverTrigger>
      <PopoverSurface className="confirm-popover" aria-label={title}>
        <Text weight="semibold" as="p">
          {title}
        </Text>
        {body && (
          <Text as="p" className="muted">
            {body}
          </Text>
        )}
        {error && (
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
        )}
        <div className="confirm-popover-actions">
          <Button size="small" onClick={() => setOpen(false)}>
            {t("common.cancel")}
          </Button>
          <Button
            size="small"
            appearance="primary"
            className="danger-primary"
            icon={icon ?? <Delete20Regular />}
            disabled={pending}
            onClick={() => {
              setPending(true);
              setError(null);
              onConfirm()
                .then(() => setOpen(false))
                .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
                .finally(() => setPending(false));
            }}
          >
            {confirmLabel ?? t("common.remove")}
          </Button>
        </div>
      </PopoverSurface>
    </Popover>
  );
}
