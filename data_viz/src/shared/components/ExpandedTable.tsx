/**
 * ExpandedTable — the globally shared data-driven table: the expanded-table
 * style (primary.main sticky header, zebra hover rows, bordered rounded
 * scroll container) + the shared per-column header filters (ticks / date /
 * numeric range, opt-in per column) + LAYERED HEADERS.
 *
 * Layered header: a column with `group` renders its label+filter in the
 * SUB-header row, merged under one group header cell (colSpan) in the top
 * row together with every contiguous column sharing the same group label.
 * Columns WITHOUT `group` span both rows (rowSpan 2). Groups must therefore
 * be contiguous in `columns` (caller's responsibility).
 *
 * Column spec:
 *   • key/label         — identity + header text (also the filter label).
 *   • align             — body-cell AND header text alignment (default left).
 *   • width             — fixed width hint on header + body cells (columns
 *     settle compact/even instead of jittering with content).
 *   • render            — custom cell content; default shows the filter
 *     value ("—" when null) or nothing.
 *   • group             — layered-header group label (see above).
 *   • filter            — opt-in: { type: "ticks" | "date" | "range",
 *     granularity?, value(row) }. Filters AND across columns; tick menus
 *     are tri-state ((All) ⇔ everything shown); state resets when
 *     `filterScopeDeps` change.
 *
 * Rows are zebra-striped; when every row is filtered out a single muted
 * row says so; when `rows` is empty from the start `emptyState` renders.
 */
import { type ReactNode } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import useTableHeaderFilters, { type HeaderFilterDef } from "@/hooks/table-header-filters";
import {
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
} from "@/shared/styles/expanded-table-styles";

export interface ExpandedTableFilter<T> {
  type: "ticks" | "date" | "range";
  /** date type only — input granularity (default "date"). */
  granularity?: "date" | "month";
  /** The row's filterable value (ticks: string; date: comparable
   *  "YYYY-MM"/"YYYY-MM-DD"; range: number). */
  value: (row: T) => string | number | null;
}

export interface ExpandedTableColumn<T> {
  key: string;
  label: string;
  /** Cell AND header text alignment (default left) — headers align with
   *  their values so numeric columns read as one visual column. */
  align?: "left" | "center" | "right";
  /** Fixed width hint for the column (CSS px / any CSS width), applied to
   *  BOTH the header and body cells so auto table-layout settles compact,
   *  even columns instead of jittering per content. */
  width?: number | string;
  render?: (row: T) => ReactNode;
  /** Layered-header group label — contiguous same-label columns merge under
   *  one top-row group cell. */
  group?: string;
  filter?: ExpandedTableFilter<T>;
}

export interface ExpandedTableProps<T> {
  columns: ExpandedTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Scroll-viewport height in px (sticky header enabled). */
  maxHeight?: number;
  /** Reset all column filters when these deps change (scope change). */
  filterScopeDeps?: unknown[];
  /** Rendered instead of the table when `rows` is empty. */
  emptyState?: ReactNode;
}

/** Top header-row height (px) — fixed so the sub-header row's sticky top
 *  offset matches; both controlled via the head-cell sx below. */
const HEAD_ROW_H = 28;

export function ExpandedTable<T>({
  columns,
  rows,
  rowKey,
  maxHeight,
  filterScopeDeps = [],
  emptyState,
}: ExpandedTableProps<T>) {
  const filterDefs: HeaderFilterDef<T>[] = columns
    .filter((c) => c.filter != null)
    .map((c) => ({
      key: c.key,
      label: c.label,
      type: c.filter!.type,
      granularity: c.filter!.granularity,
      value: c.filter!.value,
    }));
  const { filtered, menuFor } = useTableHeaderFilters(filterDefs, rows, filterScopeDeps);

  if (rows.length === 0 && emptyState != null) {
    return <>{emptyState}</>;
  }

  const layered = columns.some((c) => c.group != null);
  // Contiguous group runs — standalone columns keep their own run (group null).
  const runs: Array<{ group: string | null; cols: ExpandedTableColumn<T>[] }> = [];
  for (const c of columns) {
    const last = runs[runs.length - 1];
    if (c.group != null && last != null && last.group === c.group) {
      last.cols.push(c);
    } else {
      runs.push({ group: c.group ?? null, cols: [c] });
    }
  }

  const headSx = { ...expandedTableHeadCellSx, height: HEAD_ROW_H, py: 0.4 } as const;
  const subSx = { ...expandedTableHeadCellSx, position: "sticky", top: HEAD_ROW_H, py: 0.4 } as const;
  const groupSx = { ...headSx, textAlign: "center" } as const;

  /** Header/body cell sx for one column — the column's alignment plus the
   *  fixed width hint (kept off group colSpan cells, which size to the sum
   *  of their sub-columns). */
  const colSx = (
    c: ExpandedTableColumn<T>,
    base: Record<string, unknown>,
  ): Record<string, unknown> => ({
    ...base,
    textAlign: c.align ?? "left",
    ...(c.width != null ? { width: c.width } : {}),
  });

  const headContent = (c: ExpandedTableColumn<T>, def?: HeaderFilterDef<T>): ReactNode =>
    c.filter != null && def != null ? menuFor(def) : c.label;

  const defByKey = new Map(filterDefs.map((d) => [d.key, d]));

  const cellText = (c: ExpandedTableColumn<T>, row: T): ReactNode => {
    if (c.render != null) return c.render(row);
    if (c.filter == null) return null;
    const v = c.filter.value(row);
    return v == null ? (
      <Typography component="span" variant="inherit" color="text.disabled">
        —
      </Typography>
    ) : (
      String(v)
    );
  };

  return (
    <TableContainer sx={expandedTableContainerSx(maxHeight)}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            {runs.map((run) =>
              run.group == null ? (
                <TableCell
                  key={run.cols[0].key}
                  sx={colSx(run.cols[0], {
                    ...headSx,
                    ...(layered ? { position: "sticky", top: 0 } : {}),
                  })}
                  rowSpan={layered ? 2 : undefined}
                >
                  {headContent(run.cols[0], defByKey.get(run.cols[0].key))}
                </TableCell>
              ) : (
                <TableCell
                  key={`g-${run.group}`}
                  sx={{ ...groupSx, position: "sticky", top: 0 }}
                  colSpan={run.cols.length}
                >
                  {run.group}
                </TableCell>
              ),
            )}
          </TableRow>
          {layered && (
            <TableRow>
              {runs.flatMap((run) =>
                run.group == null
                  ? []
                  : run.cols.map((c) => (
                      <TableCell key={c.key} sx={colSx(c, subSx)}>
                        {headContent(c, defByKey.get(c.key))}
                      </TableCell>
                    )),
              )}
            </TableRow>
          )}
        </TableHead>
        <TableBody>
          {filtered.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} sx={expandedTableBodyCellSx}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.68rem" }}>
                  all rows filtered out — clear a header filter
                </Typography>
              </TableCell>
            </TableRow>
          ) : (
            filtered.map((row, idx) => (
              <TableRow key={rowKey(row)} sx={expandedTableBodyRowSx(idx)}>
                {columns.map((c) => (
                  <TableCell
                    key={c.key}
                    sx={colSx(c, expandedTableBodyCellSx)}
                  >
                    {cellText(c, row)}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default ExpandedTable;
