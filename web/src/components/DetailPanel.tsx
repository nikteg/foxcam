import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { useEffect, useRef, type ReactNode } from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

/** Clicks here don't close the panel: list rows (they pick another item) and anything
 * opened from it (menus, pickers, tooltips, a dialog). */
const KEEP_OPEN =
  ".dt-row, [role=dialog], [role=menu], [role=listbox], .fui-Popover, " +
  ".fui-PopoverSurface, .fui-Tooltip";

/**
 * Detail view for the selected list item: a drawer from the right, like every dialog. Not
 * modal, so another row of the list can be picked while it is open; a click anywhere else
 * outside it closes it.
 */
export function DetailPanel({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const phone = useMediaQuery(PHONE);
  const { t } = useI18n();
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target || panel.current?.contains(target) || target.closest(KEEP_OPEN)) return;
      closeRef.current();
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return (
    <OverlayDrawer
      ref={panel}
      open={open}
      position="end"
      size={phone ? "full" : "medium"}
      modalType="non-modal"
      onOpenChange={(_, d) => !d.open && onClose()}
      className="app-drawer"
    >
      <DrawerHeader>
        <DrawerHeaderTitle
          action={
            <Button
              appearance="subtle"
              aria-label={t("common.close")}
              icon={<Dismiss24Regular />}
              onClick={onClose}
            />
          }
        >
          {title}
        </DrawerHeaderTitle>
      </DrawerHeader>
      <DrawerBody className="detail-body">{children}</DrawerBody>
    </OverlayDrawer>
  );
}
