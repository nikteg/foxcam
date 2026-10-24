import { Select } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { useI18n } from "../i18n";

export function useProfiles() {
  return useQuery({ queryKey: ["profiles"], queryFn: api.profiles, refetchInterval: 15_000 });
}

/** Display name for a profile id (falls back to the id for removed profiles). */
export function useProfileName(): (id: string | null | undefined) => string {
  const { data = [] } = useProfiles();
  return (id) => data.find((p) => p.id === (id ?? "default"))?.display_name ?? id ?? "default";
}

export function ProfileSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  const { data: profiles = [] } = useProfiles();
  if (profiles.length < 2 && !value) return null; // nothing to choose between
  return (
    <Select value={value} onChange={(_, d) => onChange(d.value)} aria-label={t("profiles.profile")}>
      <option value="">{t("profiles.all")}</option>
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.display_name}
        </option>
      ))}
    </Select>
  );
}
