import { useQuery } from "@tanstack/react-query";

/** The item a URL opens (a drawer's `?selected=` and the like): from the list on the page,
 * or, when the list's filters or length leave it out (a link, a filter changed since),
 * fetched on its own. Its query sits under the list's key (`listKey`, e.g. "events"), so
 * whatever refreshes the list refreshes it too. */
export function useLinked<T extends { id: number }>(
  list: T[] | undefined,
  id: number | null,
  listKey: string,
  fetchOne: (id: number) => Promise<T>,
): T | undefined {
  const inList = id === null ? undefined : list?.find((item) => item.id === id);
  const one = useQuery({
    queryKey: [listKey, "one", id],
    queryFn: () => fetchOne(id!),
    // Once the list is there without it.
    enabled: id !== null && !!list && !inList,
    retry: false,
  });
  return inList ?? (one.data?.id === id ? one.data : undefined);
}
