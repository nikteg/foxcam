import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useMe } from "../auth";
import { useI18n } from "../i18n";
import { Empty, RouterLink } from "./ui";

/** True once the cameras are known and there are none this account may see. */
export function useNoCameras(): boolean {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  return cameras.data?.length === 0;
}

/** What a page shows instead of its lists while the account has no cameras: how to get
 *  one (add your own, or have one shared with you), rather than empty lists and filters. */
export function NoCameras() {
  const { t, rich } = useI18n();
  const me = useMe();
  return (
    <Empty>
      {me?.can_add_cameras
        ? rich("cameras.noneYet", {
            link: <RouterLink to="/cameras">{t("dashboard.addCameraLink")}</RouterLink>,
          })
        : t("cameras.noneShared")}
    </Empty>
  );
}
