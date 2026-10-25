import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
  Portal,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

/** Clicks here don't close the panel: list rows and event cards' picture and title (they
 * pick another item) and anything opened from it (menus, pickers, tooltips, a dialog). */
const KEEP_OPEN =
  ".dt-row, .event-card .thumb-button, .event-card .event-title, [role=dialog], " +
  "[role=menu], [role=listbox], .fui-Popover, .fui-PopoverSurface, .fui-Tooltip";

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
  // Where the drawer is mounted: after the backdrop in the same portal. Every portal stacks
  // on its own, so in separate ones a backdrop opened later would cover the drawer.
  const [host, setHost] = useState<HTMLDivElement | null>(null);
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
    <>
      {/* The same dimmed page as behind the modal drawers, but clicks go through it:
          rows stay clickable (they pick another item). */}
      {/* Fluent's Portal: inside the theme (its colours), like the drawers. */}
      <Portal>
        {open && <div className="detail-backdrop" aria-hidden="true" />}
        <div ref={setHost} className="detail-host" />
      </Portal>
      <OverlayDrawer
        ref={panel}
        open={open && !!host}
        mountNode={host}
        position="end"
        size={phone ? "full" : "medium"}
        modalType="non-modal"
        // Phones: the drawer covers the screen, so it just appears (no slide).
        surfaceMotion={phone ? null : undefined}
        backdropMotion={phone ? null : undefined}
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
    </>
  );
}
