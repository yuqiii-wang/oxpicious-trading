/**
 * ConfigServiceTable — shared table shell for the config pages: a Paper
 * holding a named header box (table name + visible/total row count, with
 * the DirPathFilterSelect subtree filter (unless hidden), the column-value
 * ValueFilterSelect dropdowns, and the search box pushed right) above the
 * <Table> itself.
 *
 * Owns the search + directory + column-filter state and applies all of them
 * to `rows` — including the dir-staleness collapse after a save/delete
 * removes the selected directory (which also turns a `defaultDir` the tree
 * doesn't contain back into no-filter; a column value whose last row went
 * away likewise falls back to no-filter) — handing the filtered rows (and
 * the current search query, for empty-state copy) to the `children` render
 * prop. The <TableHead> and <TableBody> stay page-specific since each
 * config table's columns and row actions differ.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Box, CircularProgress, Paper, Table, TextField, Typography } from "@mui/material";
import DirPathFilterSelect, {
  buildDirPaths,
  serviceInDir,
} from "@/config/components/DirPathFilterSelect";
import ValueFilterSelect, {
  type ValueFilterOption,
} from "@/shared/components/ValueFilterSelect";

/** One column-value dropdown over a closed vocabulary derived from the
 *  rows (freq, JSON file, …). */
interface ColumnFilter<R> {
  /** Field label shown on the closed select. */
  label: string;
  /** Row → filterable value. */
  rowValue: (r: R) => string;
  /** Fixed pixel width of the dropdown (default 150 — long values like
   *  file names need more). */
  width?: number;
}

interface ConfigServiceTableProps<R extends { service: string }> {
  /** Table name shown at the left of the header box. */
  name: string;
  /** Loaded rows, unfiltered — null while loading. */
  rows: readonly R[] | null;
  /** Row text the search box matches (service + request type, typically). */
  searchText: (r: R) => string;
  /** Column-value dropdowns, left to right (each a ValueFilterSelect whose
   *  options derive from the rows). */
  filters?: ColumnFilter<R>[];
  /** The dotted-directory subtree dropdown — false on pages whose rows
   *  don't key on a dotted tree path. Default true. */
  showDirFilter?: boolean;
  /** Cap the directory dropdown at this many dotted layers (undefined = all). */
  maxDepth?: number;
  /** Directory filter preselected on mount — ignored when the tree doesn't
   *  contain it (falls back to "(all)"). */
  defaultDir?: string;
  /** Column-filter values preselected on mount (label → value) — each is
   *  ignored when the rows don't contain it (the same staleness rule as
   *  defaultDir drops it back to "(all)"). */
  defaultFilters?: Record<string, string>;
  /** Search input placeholder. */
  searchPlaceholder?: string;
  /** Page-level action node rendered right after the row count in the
   *  header box (e.g. a Run button) — before the flexGrow spacer that
   *  pushes the filters right. */
  action?: ReactNode;
  /** Notified on mount and whenever the ACTIVE column-filter values
   *  change (staleness-resolved; "" = all) — pages keying an action off
   *  a filter selection (the orchestration tier Run) read the active
   *  selection here. */
  onActiveFiltersChange?: (active: Record<string, string>) => void;
  children: (visible: R[], query: string) => ReactNode;
}

export default function ConfigServiceTable<R extends { service: string }>({
  name,
  rows,
  searchText,
  filters = [],
  showDirFilter = true,
  maxDepth,
  defaultDir,
  defaultFilters,
  searchPlaceholder = "Search services…",
  action,
  onActiveFiltersChange,
  children,
}: ConfigServiceTableProps<R>) {
  const [search, setSearch] = useState("");
  // Nested-directory subtree filter over the dotted service names, preset
  // to `defaultDir` (the staleness check below drops it if not a real dir).
  const [dir, setDir] = useState(defaultDir ?? "");
  // Column-value filters, keyed by the filter's label ("" = all), with the
  // mount-time preselection (each dropped back to "(all)" by the staleness
  // resolution below when the rows don't carry the value).
  const [filterVals, setFilterVals] = useState<Record<string, string>>(() => ({
    ...defaultFilters,
  }));

  const services = useMemo(() => (rows ?? []).map((r) => r.service), [rows]);
  const query = search.trim().toLowerCase();
  // A dir selection can go stale after a save/delete removes it — treat it
  // as no-filter then.
  const activeDir = buildDirPaths(services, maxDepth).includes(dir) ? dir : "";
  // Per-filter dropdown options (distinct values over the rows, sorted,
  // each with its row count).
  const filterOptions = useMemo(
    () =>
      filters.map((f) => {
        const counts = new Map<string, number>();
        for (const r of rows ?? []) {
          const v = f.rowValue(r);
          counts.set(v, (counts.get(v) ?? 0) + 1);
        }
        return {
          filter: f,
          options: [...counts.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([value, count]) => ({ value, count })),
        };
      }),
    [rows, filters],
  );
  // Same staleness fallback per column filter: a value whose last row went
  // away acts as no-filter.
  const activeFilterVals = Object.fromEntries(
    filterOptions.map(({ filter, options }) => [
      filter.label,
      options.some((o) => o.value === filterVals[filter.label])
        ? filterVals[filter.label]
        : "",
    ]),
  );
  // Report the active selection to the page (keyed off a filter) — the
  // serialized key keeps the effect to real value changes.
  const activeFilterKey = JSON.stringify(activeFilterVals);
  useEffect(() => {
    onActiveFiltersChange?.(JSON.parse(activeFilterKey) as Record<string, string>);
  }, [activeFilterKey, onActiveFiltersChange]);
  const visible = (rows ?? []).filter(
    (r) =>
      (!showDirFilter || serviceInDir(r.service, activeDir)) &&
      filterOptions.every(
        ({ filter }) =>
          activeFilterVals[filter.label] === "" ||
          filter.rowValue(r) === activeFilterVals[filter.label],
      ) &&
      searchText(r).toLowerCase().includes(query),
  );

  return (
    <Paper sx={{ overflowX: "auto" }}>
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 1,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">{name}</Typography>
        <Typography variant="caption" color="text.secondary">
          {rows != null ? `${visible.length} of ${rows.length} rows` : "loading…"}
        </Typography>
        {action}
        <Box sx={{ flexGrow: 1 }} />
        {showDirFilter && (
          <DirPathFilterSelect
            services={services}
            value={activeDir}
            onChange={setDir}
            maxDepth={maxDepth}
          />
        )}
        {filterOptions.map(({ filter, options }) => (
          <ValueFilterSelect
            key={filter.label}
            label={filter.label}
            options={options satisfies ValueFilterOption[]}
            value={activeFilterVals[filter.label] ?? ""}
            onChange={(v) => setFilterVals((m) => ({ ...m, [filter.label]: v }))}
            width={filter.width}
          />
        ))}
        <TextField
          size="small"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={searchPlaceholder}
          sx={{ width: 260 }}
        />
      </Box>
      {/* Rows still loading → centered spinner instead of an empty shell. */}
      {rows == null ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <Table size="small">{children(visible, search.trim())}</Table>
      )}
    </Paper>
  );
}
