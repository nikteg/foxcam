import { useSearchParams } from "react-router";

/** Page filters kept in the URL so views are linkable and survive reloads. */
export function useFilters<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""])) as Record<K, string>;
  const set = (key: K, value: string | boolean) => {
    const next = new URLSearchParams(params);
    const text = typeof value === "boolean" ? (value ? "true" : "") : value;
    if (text) next.set(key, text);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  return [values, set] as const;
}
