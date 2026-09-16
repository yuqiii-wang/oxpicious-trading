/**
 * ExpandedTable — the globally shared data-driven table: the expanded-table
 * style (primary.main sticky header, zebra hover rows, bordered rounded
 * scroll container) + the shared per-column header filters (ticks / date /
 * numeric range, opt-in per column) + LAYERED HEADERS.
 *
 * Header filters are DISABLED BY DEFAULT — they only render when the caller
 * explicitly passes `enableFilters` (sourced from the backend response's
 * `enable_filters` arg). Column `filter` defs alone are not enough: without
 * `enableFilters` the headers show plain labels and no filtering runs.
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
 *   • filter            — { type: "ticks" | "date" | "range",
 *     granularity?, frozenFromYears?, value(row) }. Only active when
 *     `enableFilters` is set;
 *     its value(row) ALSO feeds the default cell text regardless. When
 *     active, filters AND across columns and tick menus are tri-state
 *     ((All) ⇔ everything shown); state resets when `filterScopeDeps`
 *     change. Each filter popup also carries an order row (Ascending ⇄
 *     Descending) that makes its column the table's ORDERING KEY — rows
 *     render sorted by it, defaulting to the first date column descending.
 *
 * Rows are zebra-striped; when every row is filtered out a single muted
 * row says so; when `rows` is empty from the start `emptyState` renders.
 */
import { memo, useMemo, type ReactNode } from "react";
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
  /** date type only — END-ONLY mode: a single editable end-period
   *  input selecting rows whose stats month EQUALS it (the end − N
   *  years lookback min is auto-frozen as a caption-only stats
   *  window; no From input). The filter STARTS ACTIVE on every scope
   *  reset, seeded at `defaultEndMonth` (else the data's latest
   *  month). */
  frozenFromYears?: number;
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
  /** Master switch for the per-column header filters — DISABLED by
   *  default. Pass true (typically from the backend response's
   *  `enable_filters` arg) to render the filter menus; column `filter`
   *  defs alone never enable them. */
  enableFilters?: boolean;
  /** Reset all column filters when these deps change (scope change). */
  filterScopeDeps?: unknown[];
  /** END-ONLY date filters (frozenFromYears) seed their end bound at
   *  this period on every scope reset, instead of the data's latest —
   *  keep it in filterScopeDeps so a new value re-seeds. Null/undefined
   *  = latest month. */
  defaultEndMonth?: string | null;
  /** Rendered instead of the table when `rows` is empty. */
  emptyState?: ReactNode;
  /** Row-click handler (cursor turns pointer when set). */
  onRowClick?: (row: T) => void;
  /** rowKey of the currently selected row (tinted) — paired with
   *  onRowClick to show which row the selection state belongs to. */
  selectedRowKey?: string | null;
}

/** Top header-row height (px) — fixed so the sub-header row's sticky top
 *  offset matches; both controlled via the head-cell sx below. */
const HEAD_ROW_H = 28;

/** Header/body cell sx for one column — the column's alignment plus the
 *  fixed width hint (kept off group colSpan cells, which size to the sum
 *  of their sub-columns). Module scope: shared by the header and the
 *  memoized body rows without identity churn. */
function colSx<T>(
  c: ExpandedTableColumn<T>,
  base: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...base,
    textAlign: c.align ?? "left",
    ...(c.width != null ? { width: c.width } : {}),
  };
}

/** One body row's default cell content: the column's render fn, else the
 *  filter value ("—" when null), else nothing. */
function cellText<T>(
  c: ExpandedTableColumn<T>,
  row: T,
  valueByKey: Map<string, (r: T) => string | number | null>,
): ReactNode {
  if (c.render != null) return c.render(row);
  const valueFn = valueByKey.get(c.key);
  if (valueFn == null) return null;
  const v = valueFn(row);
  return v == null ? (
    <Typography component="span" variant="inherit" color="text.disabled">
      —
    </Typography>
  ) : (
    String(v)
  );
}

interface BodyRowProps<T> {
  row: T;
  columns: ExpandedTableColumn<T>[];
  /** column key → filter value fn (feeds default cell text). */
  valueByKey: Map<string, (r: T) => string | number | null>;
  selected: boolean;
  idx: number;
  onRowClick?: (row: T) => void;
}

/** One body row, individually MEMOIZED: a selection-tint change
 *  (selectedRowKey) or a freeze flip re-renders only the affected row
 *  instead of the whole 100+ row body (the tint render measured ~0.9 s
 *  on the Recent Movements forecast table, 2026-09). All props stay
 *  identity-stable for untouched rows (memoized columns in callers). */
function BodyRowImpl<T>({ row, columns, valueByKey, selected, idx, onRowClick }: BodyRowProps<T>) {
  return (
    <TableRow
      onClick={onRowClick ? () => onRowClick(row) : undefined}
      sx={{
        ...expandedTableBodyRowSx(idx),
        ...(onRowClick ? { cursor: "pointer" } : {}),
        ...(selected ? { bgcolor: "action.selected" } : {}),
      }}
    >
      {columns.map((c) => (
        <TableCell key={c.key} sx={colSx(c, expandedTableBodyCellSx)}>
          {cellText(c, row, valueByKey)}
        </TableCell>
      ))}
    </TableRow>
  );
}
const BodyRow = memo(BodyRowImpl) as typeof BodyRowImpl;

export function ExpandedTableImpl<T>({
  columns,
  rows,
  rowKey,
  maxHeight,
  enableFilters = false,
  filterScopeDeps = [],
  defaultEndMonth = null,
  emptyState,
  onRowClick,
  selectedRowKey,
}: ExpandedTableProps<T>) {
  // Filters are opt-in via enableFilters (default false — "explicitly said
  // to set up filter args from backend"). Column defs alone are not enough.
  // The value fns stay reachable for DEFAULT CELL TEXT even when the filter
  // UI is off, so render-less columns keep showing their values.
  // Memoized on `columns`: identity stability here keeps the header-filter
  // hook's memos AND the memoized body rows from recomputing on every
  // parent render.
  const filterDefs: HeaderFilterDef<T>[] = useMemo(
    () =>
      enableFilters
        ? columns
            .filter((c) => c.filter != null)
            .map((c) => ({
              key: c.key,
              label: c.label,
              type: c.filter!.type,
              granularity: c.filter!.granularity,
              frozenFromYears: c.filter!.frozenFromYears,
              value: c.filter!.value,
            }))
        : [],
    [columns, enableFilters],
  );
  const valueByKey = useMemo(
    () =>
      new Map(
        columns
          .filter((c) => c.filter != null)
          .map((c) => [c.key, c.filter!.value] as const),
      ),
    [columns],
  );
  const { filtered, menuFor } = useTableHeaderFilters(
    filterDefs,
    rows,
    filterScopeDeps,
    defaultEndMonth,
  );

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

  const headContent = (c: ExpandedTableColumn<T>, def?: HeaderFilterDef<T>): ReactNode =>
    c.filter != null && def != null ? menuFor(def) : c.label;

  const defByKey = new Map(filterDefs.map((d) => [d.key, d]));

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
            filtered.map((row, idx) => {
              const key = rowKey(row);
              return (
                <BodyRow<T>
                  key={key}
                  row={row}
                  columns={columns}
                  valueByKey={valueByKey}
                  selected={selectedRowKey != null && key === selectedRowKey}
                  idx={idx}
                  onRowClick={onRowClick}
                />
              );
            })
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

// Memoized — callers that memoize their column defs / rowKey / callbacks
// (ForecastTable) keep this heavy 100+ row body out of unrelated parent
// re-renders (trigger-day freeze / chip state flips).
export const ExpandedTable = memo(ExpandedTableImpl) as typeof ExpandedTableImpl;

export default ExpandedTable;
