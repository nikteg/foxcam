import {
  Button,
  createTableColumn,
  DataGrid,
  DataGridBody,
  DataGridCell,
  DataGridHeader,
  DataGridHeaderCell,
  DataGridRow,
  type TableColumnSizingOptions,
} from "@fluentui/react-components";
import {
  memo,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

export interface Column<T> {
  id: string;
  header: string;
  cell: (item: T) => ReactNode;
  /** Makes the column sortable. */
  compare?: (a: T, b: T) => number;
  /** Ideal width in px on desktop (columns can be resized by dragging the header edge). */
  width?: number;
  minWidth?: number;
  /** How the cell behaves in the phone card layout. */
  phone?: "full" | "hide" | "thumb" | "nolabel";
}

interface Props<T> {
  items: T[];
  columns: Column<T>[];
  getRowId: (item: T) => string | number;
  ariaLabel: string;
  onRowClick?: (item: T) => void;
  selectedId?: string | number | null;
  rowClassName?: (item: T) => string | undefined;
  defaultSort?: { column: string; direction: "ascending" | "descending" };
}

/** Rows rendered at first, and added each time the end of the table scrolls into view. */
const PAGE = 100;

type SortState = { sortColumn: string | undefined; sortDirection: "ascending" | "descending" };

/**
 * Fluent DataGrid with sortable, resizable columns on desktop. On phones the same markup
 * is restyled into stacked cards (see `.data-table` in styles.css).
 *
 * Built for long lists: rows are memoized (selecting a row re-renders two rows, not all
 * of them) and rendered a page at a time as the user scrolls. Sorting is done here, over
 * all items, so the first page is the right one.
 */
export function DataTable<T>({
  items,
  columns,
  getRowId,
  ariaLabel,
  onRowClick,
  selectedId,
  rowClassName,
  defaultSort,
}: Props<T>) {
  const { t } = useI18n();
  const phone = useMediaQuery(PHONE);
  const [sort, setSort] = useState<SortState>({
    sortColumn: defaultSort?.column,
    sortDirection: defaultSort?.direction ?? "ascending",
  });
  const [shown, setShown] = useState(PAGE);
  const gridColumns = columns.map((c) =>
    createTableColumn<T>({
      columnId: c.id,
      compare: c.compare,
      renderHeaderCell: () => c.header,
      renderCell: c.cell,
    }),
  );
  const sizing: TableColumnSizingOptions = Object.fromEntries(
    columns.map((c) => [
      c.id,
      { idealWidth: c.width ?? 120, minWidth: c.minWidth ?? Math.round((c.width ?? 100) * 0.85) },
    ]),
  );
  const compare = columns.find((c) => c.id === sort.sortColumn)?.compare;
  const sorted = compare
    ? items.toSorted((a, b) => (sort.sortDirection === "ascending" ? 1 : -1) * compare(a, b))
    : items;
  const visible = sorted.slice(0, shown);

  // The latest click handler, behind a stable function: memoized rows keep it.
  const clickRef = useRef(onRowClick);
  useLayoutEffect(() => {
    clickRef.current = onRowClick;
  });
  const [activate] = useState(() => (e: MouseEvent | KeyboardEvent, item: T) => {
    // Let buttons and links inside the row do their own thing.
    if ((e.target as HTMLElement).closest("a, button, input, [role=checkbox]")) return;
    clickRef.current?.(item);
  });

  // More rows when the end of the table comes near.
  const sentinel = useRef<HTMLDivElement>(null);
  const more = sorted.length > shown;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !more) return;
    const io = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && setShown((n) => n + PAGE),
      { rootMargin: "800px" },
    );
    io.observe(el);
    return () => io.disconnect();
    // `shown`: observe again after each page, which reports the sentinel if still in view.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [more, shown]);

  return (
    // Columns keep a readable minimum width; narrow screens scroll the table sideways.
    <div className="data-table-scroll">
      <DataGrid
        className={`data-table ${onRowClick ? "clickable" : ""}`}
        items={visible}
        columns={gridColumns}
        getRowId={getRowId}
        sortable={columns.some((c) => c.compare)}
        sortState={sort}
        onSortChange={(_, s) => setSort(s as SortState)}
        // Fixed pixel widths would break the phone card layout.
        resizableColumns={!phone}
        columnSizingOptions={sizing}
        focusMode="composite"
        aria-label={ariaLabel}
      >
        <DataGridHeader>
          <DataGridRow>
            {({ renderHeaderCell }) => (
              <DataGridHeaderCell>{renderHeaderCell()}</DataGridHeaderCell>
            )}
          </DataGridRow>
        </DataGridHeader>
        <DataGridBody<T>>
          {({ item, rowId }) => (
            <Row<T>
              key={rowId}
              item={item}
              columns={columns}
              className={["dt-row", rowClassName?.(item), rowId === selectedId ? "selected" : ""]
                .filter(Boolean)
                .join(" ")}
              selected={onRowClick ? rowId === selectedId : undefined}
              activate={activate}
            />
          )}
        </DataGridBody>
      </DataGrid>
      {more && (
        <div ref={sentinel} className="data-table-more">
          <Button appearance="subtle" onClick={() => setShown((n) => n + PAGE)}>
            {t("table.showMore", { n: sorted.length - shown })}
          </Button>
        </div>
      )}
    </div>
  );
}

interface RowProps<T> {
  item: T;
  columns: Column<T>[];
  className: string;
  selected: boolean | undefined;
  activate: (e: MouseEvent | KeyboardEvent, item: T) => void;
}

function RowImpl<T>({ item, columns, className, selected, activate }: RowProps<T>) {
  return (
    <DataGridRow<T>
      className={className}
      onClick={(e: MouseEvent) => activate(e, item)}
      onKeyDown={(e: KeyboardEvent) => e.key === "Enter" && activate(e, item)}
      aria-selected={selected}
    >
      {({ renderCell, columnId }) => {
        const col = columns.find((c) => c.id === String(columnId));
        const mode = col?.phone;
        return (
          <DataGridCell
            className={`dt-cell ${mode ? `phone-${mode}` : ""}`}
            data-label={mode === "nolabel" || mode === "thumb" ? undefined : col?.header}
          >
            {renderCell(item)}
          </DataGridCell>
        );
      }}
    </DataGridRow>
  );
}

// Re-renders only when its item (react-query keeps unchanged ones), columns or selection
// change.
const Row = memo(RowImpl) as typeof RowImpl;

export const byString =
  <T,>(get: (item: T) => string | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? "").localeCompare(get(b) ?? "");

export const byNumber =
  <T,>(get: (item: T) => number | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? -Infinity) - (get(b) ?? -Infinity);
