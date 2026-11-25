/**
 * TableWorkloadTable — the DB tab's main panel: one row per MAIN table
 * (partition families arrive as ONE aggregated parent row — rates and
 * deltas summed over the family, see DbTableEntry.partitionCount),
 * ordered SERVER-SIDE by the panel's order selector (workload heaviness |
 * recent activity — the fetch param lives on the page). Client-side
 * pagination over the returned window (TablePagination,
 * TrainLogsDialog precedent). The write-rate cell turns amber/red with
 * the flag (the ProcessRow overdue-cell precedent), the flag Chip follows
 * the DateBreakdown statusColor precedent.
 *
 * SOURCE-AWARE like the page: live mode reads the per-pass audit's
 * trailing window (the hours prop); ARCHIVE mode (source=archive) reads
 * the daily rollup table's LIFETIME aggregate — the deltas become
 * lifetime sums, the stamps day-level (rendered as the GMT operational
 * day's calendar date instead of a pass time), and the lazy partition
 * expansion fetches the rollup's per-partition lifetime aggregates too.
 *
 * Rows are EXPANDABLE: a parent row with partitions shows a partition
 * count next to its name and CLICKING IT toggles the family's expansion
 * (the user's expand affordance — not a trend select; the parent itself
 * is a write-through idle table, its own per-table trend would be empty).
 * The expansion carries the parent's AGGREGATED audit: the family trend
 * chart (GET /db/trend sums the partitions per pass for a parent) above
 * the partition list. Partitions load LAZILY per family on first expand
 * (GET /db/partitions via fetchDbPartitions — the panel loads main tables
 * only), refetched when the window selector moves while rows stay open,
 * and cached while closed. Partition sub-rows render with the same
 * columns (static rows — the former click-to-drill into the page-level
 * trend chart retired with that chart; the family aggregate above is the
 * trend surface).
 *
 * The table renders with a FIXED layout (colgroup-pinned percentage
 * columns, the name column taking the remainder): it always fits its
 * container's width — no horizontal scrollbar; long names and metrics
 * clip to an ellipsis (hover shows the full name) instead of widening
 * the panel.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Box,
  Chip,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  KeyboardArrowDown as ExpandedIcon,
  KeyboardArrowRight as CollapsedIcon,
} from "@mui/icons-material";
import type { DbAuditSource, DbTableEntry } from "@/lib/api-client/monitor";
import { fetchDbPartitions } from "@/lib/api-client/monitor";
import { fmtBytes, fmtCount, fmtRate } from "./fmt";
import { fmtDate } from "@/shared/datetime";
import DbTrendChart from "./DbTrendChart";

/** Flag chip vocabulary — 0 default / 1 amber (warning) / 2 red (error);
 *  null = not evaluated (baseline pass / replica-only row). */
function flagChip(flag: number | null): { color: "default" | "warning" | "error"; label: string } {
  if (flag === 2) return { color: "error", label: "red" };
  if (flag === 1) return { color: "warning", label: "amber" };
  if (flag === 0) return { color: "default", label: "ok" };
  return { color: "default", label: "—" };
}

/** The last-active stamp cell: live rows show the pass time (browser
 *  local HH:mm); archive rows show the GMT operational DAY as a calendar
 *  date — the rollup's grain — so the UTC date parts are the honest
 *  rendering (a browser-local date could skew a day off the archive's
 *  UTC-day boundary, Beijing 08:00). */
function stamp(iso: string | null, source: DbAuditSource): string {
  if (!iso) return "—";
  if (source === "archive") {
    const d = new Date(iso);
    // UTC date parts are the honest rendering (the rollup's GMT-day
    // grain) — remapped to the display convention only.
    return fmtDate(
      `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`,
    );
  }
  return shortTime(iso);
}

function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Fixed-layout cells never spill past their column: long content clips
 *  to an ellipsis so the nine-column table always fits its container's
 *  width — the panel renders without a horizontal scrollbar. */
const CELL_CLIP = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
} as const;

/** The nine columns' fixed-layout width plan (colgroup): percentages of
 *  the container, the flexible FIRST col taking whatever remains for the
 *  table/partition name. Shared by the main table and the nested
 *  partition sub-table so both keep the same column rhythm. */
function ColWidthPlan() {
  return (
    <colgroup>
      <col />
      <col style={{ width: "8%" }} />
      <col style={{ width: "8%" }} />
      <col style={{ width: "13%" }} />
      <col style={{ width: "11%" }} />
      <col style={{ width: "8%" }} />
      <col style={{ width: "7%" }} />
      <col style={{ width: "8%" }} />
      <col style={{ width: "8%" }} />
    </colgroup>
  );
}

/** The first cell: the expand affordance for partitioned families (row
 *  click toggles — the icon is the visual cue), a partition-count chip,
 *  else the bare name. Long names clip to an ellipsis (hover shows the
 *  full name) instead of widening the table. */
function NameCell({
  row,
  expanded,
}: {
  row: DbTableEntry;
  expanded: boolean;
}) {
  const expandable = (row.partitionCount ?? 0) > 0;
  return (
    <TableCell sx={{ fontVariantNumeric: "tabular-nums", ...CELL_CLIP }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}>
        {expandable && (
          <IconButton
            size="small"
            edge="start"
            aria-label={expanded ? "collapse partitions" : "expand partitions"}
            sx={{ ml: -0.75, p: 0.25, flexShrink: 0 }}
          >
            {expanded ? <ExpandedIcon sx={{ fontSize: 18 }} /> : <CollapsedIcon sx={{ fontSize: 18 }} />}
          </IconButton>
        )}
        <Tooltip title={`${row.schema}.${row.table}`}>
          <Box
            component="span"
            sx={{
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {row.schema}.{row.table}
          </Box>
        </Tooltip>
        {expandable && (
          <Chip
            size="small"
            variant="outlined"
            label={`${row.partitionCount} parts`}
            sx={{ ml: 0.5, height: 18, fontSize: "0.7rem", flexShrink: 0 }}
          />
        )}
      </Box>
    </TableCell>
  );
}

/** One workload row's nine metric cells (shared by main rows and
 *  partition sub-rows so both stay column-aligned). SOURCE-AWARE: live
 *  rows tooltip the audit window; archive rows carry lifetime sums.
 *  Cells clip under the fixed layout (CELL_CLIP) — a narrow panel
 *  ellipsizes a long metric instead of forcing a horizontal scrollbar. */
function MetricCells({ row, source }: { row: DbTableEntry; source: DbAuditSource }) {
  const chip = flagChip(row.writeFlag);
  return (
    <>
      <TableCell
        align="right"
        sx={{
          ...CELL_CLIP,
          fontVariantNumeric: "tabular-nums",
          color:
            row.writeFlag === 2
              ? "error.main"
              : row.writeFlag === 1
                ? "warning.main"
                : undefined,
        }}
      >
        {fmtRate(row.writeRate)}
      </TableCell>
      <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
        {fmtRate(row.readRate)}
      </TableCell>
      <Tooltip
        title={
          source === "archive"
            ? "lifetime day-sums of the rollup · snapshots the latest archived day's end"
            : `window ${row.windowS ?? "—"}s · deltas over the last active pass`
        }
      >
        <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
          {fmtCount(row.insRows)} / {fmtCount(row.updRows)} / {fmtCount(row.delRows)}
        </TableCell>
      </Tooltip>
      <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
        {fmtCount(row.nLiveTup)} / {fmtCount(row.nDeadTup)}
      </TableCell>
      <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
        {fmtBytes(row.sizeBytes)}
      </TableCell>
      <TableCell align="center" sx={CELL_CLIP}>
        <Chip size="small" color={chip.color} label={chip.label} variant="outlined" />
      </TableCell>
      <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
        {stamp(row.writeCheckedAt, source)}
      </TableCell>
      <TableCell align="right" sx={{ ...CELL_CLIP, fontVariantNumeric: "tabular-nums" }}>
        {stamp(row.readCheckedAt, source)}
      </TableCell>
    </>
  );
}

/** Per-family partition expansion state: fetched lazily on expand, keyed
 *  by family, stamped with the source + hours it was fetched under (the
 *  window selector refetches rows that stay open; hours=0 only ever
 *  occurs in archive mode, but the source is stamped explicitly). */
interface PartitionsState {
  hours: number;
  source: DbAuditSource;
  loading: boolean;
  error: string | null;
  rows: DbTableEntry[];
}

interface Props {
  rows: DbTableEntry[];
  hours: number;
  source: DbAuditSource;
}

const ROWS_PER_PAGE_OPTIONS = [25, 50, 100];

export default function TableWorkloadTable({ rows, hours, source }: Props) {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [partitions, setPartitions] = useState<Record<string, PartitionsState>>({});

  // A window / ordering refetch (or a sampler pruning idle rows) can shrink
  // the row set under the current page — follow the data back into range.
  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(rows.length / rowsPerPage) - 1);
    if (page > maxPage) setPage(maxPage);
  }, [rows.length, rowsPerPage, page]);

  const loadPartitions = useCallback(
    async (schema: string, table: string, fetchHours: number, fetchSource: DbAuditSource) => {
      const key = `${schema}.${table}`;
      setPartitions((prev) => ({
        ...prev,
        [key]: {
          hours: fetchHours,
          source: fetchSource,
          loading: true,
          error: null,
          rows: prev[key]?.rows ?? [],
        },
      }));
      try {
        const res = await fetchDbPartitions(schema, table, fetchHours, fetchSource);
        setPartitions((prev) => ({
          ...prev,
          [key]: { hours: fetchHours, source: fetchSource, loading: false, error: null, rows: res.rows },
        }));
      } catch (e) {
        setPartitions((prev) => ({
          ...prev,
          [key]: { hours: fetchHours, source: fetchSource, loading: false, error: String(e), rows: [] },
        }));
      }
    },
    [],
  );

  const toggleExpand = useCallback(
    (row: DbTableEntry) => {
      const key = `${row.schema}.${row.table}`;
      const opening = !expandedKeys.has(key);
      setExpandedKeys((prev) => {
        const next = new Set(prev);
        if (next.has(key)) {
          next.delete(key);
        } else {
          next.add(key);
        }
        return next;
      });
      // Lazy load on first expand — or when the cached rows predate the
      // current window / source (stale-hours refetch for rows that stayed
      // open is the effect below; a just-opened family fetches here).
      if (opening) {
        const cached = partitions[key];
        if (cached == null || cached.hours !== hours || cached.source !== source) {
          void loadPartitions(row.schema, row.table, hours, source);
        }
      }
    },
    [expandedKeys, partitions, hours, source, loadPartitions],
  );

  // An open family keeps its expansion across window changes, but its rows
  // were fetched under the PREVIOUS window — refetch the open ones (the
  // just-opened family is already in flight from toggleExpand; loading
  // entries are mid-fetch, not stale).
  useEffect(() => {
    for (const key of expandedKeys) {
      const cached = partitions[key];
      if (cached == null || cached.loading || (cached.hours === hours && cached.source === source)) continue;
      const [schema, table] = key.split(".");
      void loadPartitions(schema, table, hours, source);
    }
    // partitions deliberately omitted: react to the window selector only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours, source, expandedKeys, loadPartitions]);

  const pageRows = rows.slice(page * rowsPerPage, (page + 1) * rowsPerPage);

  return (
    <Paper variant="outlined">
      <TableContainer sx={{ maxHeight: 480 }}>
        <Table stickyHeader size="small" sx={{ tableLayout: "fixed", width: "100%" }}>
          <ColWidthPlan />
          <TableHead>
            <TableRow>
              <TableCell sx={CELL_CLIP}>Table</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Write</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Reads</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Ins / Upd / Del</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Live / Dead tup</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Size</TableCell>
              <TableCell align="center" sx={CELL_CLIP}>Flag</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Last write</TableCell>
              <TableCell align="right" sx={CELL_CLIP}>Last read</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {pageRows.map((r) => {
              const key = `${r.schema}.${r.table}`;
              const expandable = (r.partitionCount ?? 0) > 0;
              const expanded = expandedKeys.has(key);
              const parts = partitions[key];
              return (
                [
                  <TableRow
                    key={key}
                    hover={expandable}
                    onClick={
                      expandable
                        ? () => toggleExpand(r) // the family's expand affordance
                        : undefined
                    }
                    sx={{ cursor: expandable ? "pointer" : undefined }}
                  >
                    <NameCell row={r} expanded={expanded} />
                    <MetricCells row={r} source={source} />
                  </TableRow>,
                  expandable && expanded && (
                    <TableRow key={`${key}:parts`}>
                      <TableCell colSpan={9} sx={{ p: 0, border: 0, bgcolor: "action.hover" }}>
                        <Box sx={{ pl: 4, pr: 1, py: 1 }}>
                          {/* The family's AGGREGATED trend — the parent's own
                              audit rows are idle zeros, so the history side of
                              the parent audit must also sum the partitions
                              (GET /db/trend is family-aware for parents). */}
                          <DbTrendChart
                            schema={r.schema}
                            table={r.table}
                            hours={hours}
                            source={source}
                            height={240}
                          />
                          {parts?.loading && (
                            <Typography variant="caption" color="text.secondary">
                              loading partitions…
                            </Typography>
                          )}
                          {parts?.error != null && (
                            <Typography variant="caption" color="error">
                              failed to load partitions: {parts.error}
                            </Typography>
                          )}
                          {parts?.loading === false && parts.error == null && parts.rows.length === 0 && (
                            <Typography variant="caption" color="text.secondary">
                              no partitions (all dropped or detached)
                            </Typography>
                          )}
                          {parts?.rows.length != null && parts.rows.length > 0 && (
                            <Table
                              size="small"
                              sx={{ tableLayout: "fixed", width: "100%", bgcolor: "background.paper" }}
                            >
                              <ColWidthPlan />
                              <TableHead>
                                <TableRow>
                                  <TableCell sx={{ pl: 2, ...CELL_CLIP }}>Partition</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Write</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Reads</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Ins / Upd / Del</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Live / Dead tup</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Size</TableCell>
                                  <TableCell align="center" sx={CELL_CLIP}>Flag</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Last write</TableCell>
                                  <TableCell align="right" sx={CELL_CLIP}>Last read</TableCell>
                                </TableRow>
                              </TableHead>
                              <TableBody>
                                {parts.rows.map((p) => (
                                  <TableRow key={p.table}>
                                    <Tooltip title={`${r.schema}.${p.table}`}>
                                      <TableCell
                                        sx={{
                                          pl: 2,
                                          ...CELL_CLIP,
                                          fontVariantNumeric: "tabular-nums",
                                        }}
                                      >
                                        {r.schema}.{p.table}
                                      </TableCell>
                                    </Tooltip>
                                    <MetricCells row={p} source={source} />
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  ),
                ]
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
      <TablePagination
        component="div"
        count={rows.length}
        page={page}
        onPageChange={(_, p) => setPage(p)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => {
          setRowsPerPage(parseInt(e.target.value, 10));
          setPage(0);
        }}
        rowsPerPageOptions={ROWS_PER_PAGE_OPTIONS}
        size="small"
      />
    </Paper>
  );
}
