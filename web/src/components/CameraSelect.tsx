import { Select } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useI18n } from "../i18n";

export function CameraSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  const { data: cameras = [] } = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  return (
    <Select value={value} onChange={(_, d) => onChange(d.value)} aria-label={t("common.camera")}>
      <option value="">{t("common.allCameras")}</option>
      {cameras.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </Select>
  );
}
