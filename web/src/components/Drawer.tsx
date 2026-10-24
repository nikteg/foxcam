// Every "dialog" in the app is a drawer sliding in from the right. Same building blocks as
// Fluent's Dialog (Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent,
// DialogActions), so forms keep their structure: import these instead of Fluent's.
import {
  Button,
  DrawerBody,
  DrawerFooter,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { createContext, useContext, useEffect, type ReactElement, type ReactNode } from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

const CloseContext = createContext<() => void>(() => {});

// Open modal drawers (they can stack): the page behind does not scroll while any is open.
let locks = 0;

/** Keep the page still behind a modal drawer, also on iOS (where swiping past the end
 * of the drawer would otherwise scroll the page underneath). */
function useScrollLock(on: boolean) {
  useEffect(() => {
    if (!on) return;
    if (locks++ === 0) document.documentElement.classList.add("scroll-locked");
    return () => {
      if (--locks === 0) document.documentElement.classList.remove("scroll-locked");
    };
  }, [on]);
}

/** ``onOpenChange`` gets ``{ open: false }`` when the drawer is dismissed (Escape, the
 * backdrop, the close button), like Fluent's Dialog. */
export function Dialog({
  open,
  onOpenChange,
  size = "medium",
  modal = true,
  children,
}: {
  open: boolean;
  onOpenChange?: (event: unknown, data: { open: boolean }) => void;
  /** small 320 px, medium ~600 px, large ~940 px; phones always full width. */
  size?: "small" | "medium" | "large";
  /** false: the page behind stays usable (e.g. picking another row of a list). */
  modal?: boolean;
  children: ReactNode;
}) {
  const phone = useMediaQuery(PHONE);
  useScrollLock(open && modal);
  const close = () => onOpenChange?.(undefined, { open: false });
  return (
    <OverlayDrawer
      open={open}
      position="end"
      size={phone ? "full" : size}
      modalType={modal ? "modal" : "non-modal"}
      // Phones: the drawer covers the screen, so it just appears (no slide).
      surfaceMotion={phone ? null : undefined}
      backdropMotion={phone ? null : undefined}
      onOpenChange={(e, d) => onOpenChange?.(e, { open: d.open })}
      className="app-drawer"
    >
      {/* React Compiler memoizes `close` (it only changes with onOpenChange). */}
      {/* oxlint-disable-next-line react/jsx-no-constructed-context-values */}
      <CloseContext.Provider value={close}>{children}</CloseContext.Provider>
    </OverlayDrawer>
  );
}

/** The drawer's column (a <form> may sit between it and DialogBody). */
export function DialogSurface({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={`drawer-surface ${className ?? ""}`}>{children}</div>;
}

export function DialogBody({ children }: { children: ReactNode }) {
  return <div className="drawer-column">{children}</div>;
}

/** Header with the title and a close button (or ``action`` instead). */
export function DialogTitle({ children, action }: { children: ReactNode; action?: ReactElement }) {
  const close = useContext(CloseContext);
  const { t } = useI18n();
  return (
    <DrawerHeader>
      <DrawerHeaderTitle
        action={
          action ?? (
            <Button
              appearance="subtle"
              aria-label={t("common.close")}
              icon={<Dismiss24Regular />}
              onClick={close}
            />
          )
        }
      >
        {children}
      </DrawerHeaderTitle>
    </DrawerHeader>
  );
}

/** The scrolling middle. */
export function DialogContent({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <DrawerBody>
      <div className={`drawer-content ${className ?? ""}`}>{children}</div>
    </DrawerBody>
  );
}

/** Buttons at the bottom, right-aligned; always visible. */
export function DialogActions({ children }: { children: ReactNode }) {
  return <DrawerFooter className="drawer-actions">{children}</DrawerFooter>;
}
