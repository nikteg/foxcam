import {
  createTableColumn,
  DataGrid,
  DataGridBody,
  DataGridCell,
  DataGridHeader,
  DataGridHeaderCell,
  DataGridRow,
  type TableColumnSizingOptions,
} from "@fluentui/react-components";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";

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

/**
 * Fluent DataGrid with sortable, resizable columns on desktop. On phones the same markup
 * is restyled into stacked cards (see `.data-table` in styles.css).
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
  const phone = useMediaQuery(PHONE);
  const byId = new Map(columns.map((c) => [c.id, c]));
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

  const activate = (e: MouseEvent | KeyboardEvent, item: T) => {
    // Let buttons and links inside the row do their own thing.
    if ((e.target as HTMLElement).closest("a, button, input, [role=checkbox]")) return;
    onRowClick?.(item);
  };

  return (
    // Columns keep a readable minimum width; narrow screens scroll the table sideways.
    <div className="data-table-scroll">
      <DataGrid
        className={`data-table ${onRowClick ? "clickable" : ""}`}
        items={items}
        columns={gridColumns}
        getRowId={getRowId}
        sortable={columns.some((c) => c.compare)}
        defaultSortState={
          defaultSort
            ? { sortColumn: defaultSort.column, sortDirection: defaultSort.direction }
            : undefined
        }
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
          {({ item, rowId }) => {
            const classes = [
              "dt-row",
              rowClassName?.(item),
              rowId === selectedId ? "selected" : "",
            ];
            return (
              <DataGridRow<T>
                key={rowId}
                className={classes.filter(Boolean).join(" ")}
                onClick={(e: MouseEvent) => activate(e, item)}
                onKeyDown={(e: KeyboardEvent) => e.key === "Enter" && activate(e, item)}
                aria-selected={onRowClick ? rowId === selectedId : undefined}
              >
                {({ renderCell, columnId }) => {
                  const col = byId.get(String(columnId));
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
          }}
        </DataGridBody>
      </DataGrid>
    </div>
  );
}

export const byString =
  <T,>(get: (item: T) => string | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? "").localeCompare(get(b) ?? "");

export const byNumber =
  <T,>(get: (item: T) => number | null | undefined) =>
  (a: T, b: T) =>
    (get(a) ?? -Infinity) - (get(b) ?? -Infinity);
