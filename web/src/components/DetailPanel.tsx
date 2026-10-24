import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

/**
 * Detail view for the selected list item: a drawer from the right, like every dialog. Not
 * modal, so another row of the list can be picked while it is open.
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
  return (
    <OverlayDrawer
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
