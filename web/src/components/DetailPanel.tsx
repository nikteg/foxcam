import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  InlineDrawer,
  OverlayDrawer,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery, WIDE } from "../useMediaQuery";

/**
 * Detail view for the selected list item. Desktop: an inline panel next to the list
 * (master-detail). Tablets and phones: a drawer over the list.
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
  const wide = useMediaQuery(WIDE);
  const phone = useMediaQuery(PHONE);
  const { t } = useI18n();

  const header = (
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
  );

  if (wide) {
    return (
      <InlineDrawer open={open} position="end" separator size="medium" className="detail-inline">
        {header}
        <DrawerBody className="detail-body">{children}</DrawerBody>
      </InlineDrawer>
    );
  }
  return (
    <OverlayDrawer
      open={open}
      position="end"
      size={phone ? "full" : "medium"}
      onOpenChange={(_, d) => !d.open && onClose()}
    >
      {header}
      <DrawerBody className="detail-body">{children}</DrawerBody>
    </OverlayDrawer>
  );
}
