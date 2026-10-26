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
import { Fact, Facts, NoValue } from "./ui";

export interface Column<T> {
  id: string;
  header: string;
  cell: (item: T) => ReactNode;
  /** Makes the column sortable. */
  compare?: (a: T, b: T) => number;
  /** Ideal width in px on desktop (columns can be resized by dragging the header edge). */
  width?: number;
  minWidth?: number;
  /** On phones each row is a card of facts (label: the header). "thumb": the picture on
   * top; "nolabel": full width under the facts (e.g. buttons); "hide": left out. */
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
/** A column grows to fit its content up to this width; longer text is cut with "…". */
const MAX_FIT = 420;

type SortState = { sortColumn: string | undefined; sortDirection: "ascending" | "descending" };

/**
 * Fluent DataGrid with sortable, resizable columns on desktop. On phones a list of cards,
 * one per row, with the columns as facts (the same `Facts` as in drawers).
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
      // Nothing to show: a subtle dash, except where a picture or buttons go.
      renderCell: (item: T) => {
        const v = c.cell(item);
        if (!empty(v)) return v;
        return c.phone === "thumb" || c.phone === "nolabel" ? null : <NoValue />;
      },
    }),
  );
  // Columns grow to fit what is in them (measured after rendering, see below), so tags,
  // names and model strings are not cut off; `width` is the starting point.
  const [fitted, setFitted] = useState<Record<string, number>>({});
  const sizing: TableColumnSizingOptions = Object.fromEntries(
    columns.map((c) => {
      const width = Math.max(c.width ?? 120, fitted[c.id] ?? 0);
      return [
        c.id,
        {
          idealWidth: width,
          minWidth: Math.max(c.minWidth ?? Math.round((c.width ?? 100) * 0.85), fitted[c.id] ?? 0),
        },
      ];
    }),
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

  // Widen columns whose content does not fit (only ever wider, at most MAX_FIT px).
  // Measured after rendering and again whenever the columns change size (the grid fits
  // them to the page after its first render).
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const root = box.current;
    if (phone || !root) return;
    const measure = () => {
      const need: Record<string, number> = {};
      for (const cell of root.querySelectorAll<HTMLElement>(".dt-cell[data-col]")) {
        if (cell.scrollWidth <= cell.clientWidth + 1) continue;
        const col = cell.dataset.col!;
        need[col] = Math.max(need[col] ?? 0, Math.min(cell.scrollWidth + 4, MAX_FIT));
      }
      setFitted((f) => {
        const grow = Object.entries(need).filter(([col, w]) => w > (f[col] ?? 0));
        return grow.length ? { ...f, ...Object.fromEntries(grow) } : f;
      });
    };
    measure();
    const row = root.querySelector(".dt-row");
    const observer = new ResizeObserver(measure);
    for (const cell of row?.children ?? []) observer.observe(cell);
    return () => observer.disconnect();
    // The rows and columns are what change the content to measure; widths only grow,
    // up to MAX_FIT, so it settles.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [visible, columns, phone]);

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

  const showMore = more && (
    <div ref={sentinel} className="data-table-more">
      <Button appearance="subtle" onClick={() => setShown((n) => n + PAGE)}>
        {t("table.showMore", { n: sorted.length - shown })}
      </Button>
    </div>
  );

  if (phone) {
    return (
      <ul className="dt-cards" aria-label={ariaLabel}>
        {visible.map((item) => {
          const id = getRowId(item);
          return (
            <PhoneCard<T>
              key={id}
              item={item}
              columns={columns}
              className={[rowClassName?.(item), id === selectedId ? "selected" : ""]
                .filter(Boolean)
                .join(" ")}
              selected={id === selectedId}
              clickable={!!onRowClick}
              activate={activate}
            />
          );
        })}
        {showMore && <li>{showMore}</li>}
      </ul>
    );
  }

  return (
    // Columns keep a readable minimum width; narrow screens scroll the table sideways.
    <div className="data-table-scroll" ref={box}>
      <DataGrid
        className={`data-table ${onRowClick ? "clickable" : ""}`}
        items={visible}
        columns={gridColumns}
        getRowId={getRowId}
        sortable={columns.some((c) => c.compare)}
        sortState={sort}
        onSortChange={(_, s) => setSort(s as SortState)}
        resizableColumns
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
              className={["dt-row", rowClassName?.(item), rowId === selectedId ? "selected" : ""]
                .filter(Boolean)
                .join(" ")}
              selected={onRowClick ? rowId === selectedId : undefined}
              activate={activate}
            />
          )}
        </DataGridBody>
      </DataGrid>
      {showMore}
    </div>
  );
}

interface RowProps<T> {
  item: T;
  className: string;
  selected: boolean | undefined;
  activate: (e: MouseEvent | KeyboardEvent, item: T) => void;
}

function RowImpl<T>({ item, className, selected, activate }: RowProps<T>) {
  return (
    <DataGridRow<T>
      className={className}
      onClick={(e: MouseEvent) => activate(e, item)}
      onKeyDown={(e: KeyboardEvent) => e.key === "Enter" && activate(e, item)}
      aria-selected={selected}
    >
      {({ renderCell, columnId }) => {
        return (
          <DataGridCell className="dt-cell" data-col={String(columnId)}>
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

/** A cell without a value (columns may also return "–"). */
const empty = (v: ReactNode) =>
  v === null || v === undefined || v === false || v === "" || v === "–";

interface PhoneCardProps<T> {
  item: T;
  columns: Column<T>[];
  className: string;
  selected: boolean;
  clickable: boolean;
  activate: (e: MouseEvent | KeyboardEvent, item: T) => void;
}

/** A row on phones: picture, then the columns as facts, then full-width extras. */
function PhoneCardImpl<T>({
  item,
  columns,
  className,
  selected,
  clickable,
  activate,
}: PhoneCardProps<T>) {
  const values = columns
    .filter((c) => c.phone !== "hide")
    .map((c) => ({ c, v: c.cell(item) }))
    .filter(({ v }) => !empty(v));
  return (
    // Opened like a table row: a tap, or Enter when focused (the row's own buttons and
    // links keep working, see `activate`).
    // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <li
      className={`dt-card ${clickable ? "clickable" : ""} ${className}`}
      // oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={clickable ? 0 : undefined}
      aria-current={selected || undefined}
      onClick={(e) => activate(e, item)}
      onKeyDown={(e) => e.key === "Enter" && activate(e, item)}
    >
      {values
        .filter(({ c }) => c.phone === "thumb")
        .map(({ c, v }) => (
          <div key={c.id} className="dt-card-thumb">
            {v}
          </div>
        ))}
      <Facts>
        {values
          .filter(({ c }) => !c.phone || c.phone === "full")
          .map(({ c, v }) => (
            <Fact key={c.id} label={c.header}>
              {v}
            </Fact>
          ))}
      </Facts>
      {values
        .filter(({ c }) => c.phone === "nolabel")
        .map(({ c, v }) => (
          <div key={c.id} className="dt-card-extra">
            {v}
          </div>
        ))}
    </li>
  );
}

const PhoneCard = memo(PhoneCardImpl) as typeof PhoneCardImpl;

export const byString =
  <T,>(get: (item: T) => string | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? "").localeCompare(get(b) ?? "");

export const byNumber =
  <T,>(get: (item: T) => number | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? -Infinity) - (get(b) ?? -Infinity);
