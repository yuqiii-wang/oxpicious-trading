/**
 * DB Audit page — the Monitor app's DB tab: the per-table read/write
 * workload audit + write-heavy alerting of database/db_table_audit.py.
 *
 *   • WORKLOAD TABLE — monitor.db_table_audit (activity-sparse rows:
 *     one row per table only when its pg_stat_user_tables counters moved
 *     or its flag changed; absence = idle), fetched via
 *     GET /api/monitor/db/tables — MAIN TABLES ONLY: each partition
 *     family is one aggregated parent row (write/read rates and deltas
 *     summed over its live partitions, flag the max family severity);
 *     the partitions themselves load lazily on row click (GET
 *     /api/monitor/db/partitions). The write side comes from the primary
 *     (write_rate + the amber/red write_flag) and the read side from the
 *     replica (where the API reads). A KPI chip row summarizes the
 *     window: tables active / partitions / amber / red; the write-rate
 *     cell and flag chip amber/red with the verdict; a family row's
 *     click expands it in place.
 *
 *   • TREND — the family workload trend (Σ partitions), mounted INSIDE
 *     the expanded family row: write-rate bars (colored by each pass's
 *     flag) + read-rate line over the window (GET /api/monitor/db/trend
 *     — a partitioned parent's passes aggregate its whole family). The
 *     former page-level "workload trend" drill-down is retired — its
 *     function is replaced by this family aggregate chart.
 *
 *   • ALERTS — the write-heavy monitor log (monitor.db_table_alert via
 *     /api/monitor/db/alerts): every flag STATE TRANSITION (raise /
 *     downgrade / clear) with the tripping rate vs the threshold.
 *
 *   • SQL AUDIT — the statement-level half (monitor.sql_audit via
 *     /api/monitor/db/sql): one row per DISTINCT statement executed on
 *     the cluster (pg_stat_statements, sha256-of-normalized-text
 *     identity), sortable by total time / calls / recency / avg and
 *     searchable by text or hash prefix.
 *
 *   • INSTANCE VITALS — the latest monitor.db_health row per instance
 *     plus the trailing connections / replay-backlog trend — data the
 *     60s sampler always wrote but no UI showed before this tab.
 *
 * All fetches are live (no-store) with a window selector (1h / 6h / 24h
 * + archive) + refresh. The numeric windows read the per-pass audit
 * table's trailing window; the ARCHIVE mode swaps the store to the
 * computed daily rollup monitor.db_table_audit_archive — every workload
 * fetch carries source=archive and shows each table's LIFETIME aggregate
 * (day sums added up, peak rates, worst-ever flag, latest end-of-day
 * snapshots) over the archive's full retained history, so tables idle in
 * recent windows still show their last active day. The
 * header's VIEW TOGGLE switches the tab between its three halves —
 * table (the workload panel + the in-row family trend + alert trail) / sql
 * (the statement audit) / instance (the vitals + trend) — and the
 * workload panel's ORDER selector picks the server-side ordering
 * (workload heaviness | recent activity) before pagination.
 */
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import {
  fetchDbTables,
  type DbAuditSource,
  type DbTableOrder,
} from "@/lib/api-client/monitor";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import TableWorkloadTable from "./TableWorkloadTable";
import AlertsPanel from "./AlertsPanel";
import SqlAuditPanel from "./SqlAuditPanel";
import InstanceVitalsPanel from "./InstanceVitalsPanel";

/** The window selector: numeric trailing windows (hours) plus the
 *  ARCHIVE mode — the store itself switches (source=archive on the
 *  wire, hours=0): the computed daily rollup table's lifetime aggregate
 *  per table, so the panel spans the archive's full retained history
 *  instead of a trailing window of the per-pass audit. */
type WindowSel = 1 | 6 | 24 | "archive";

const WINDOWS: WindowSel[] = [1, 6, 24, "archive"];

function windowLabel(w: WindowSel): string {
  return w === "archive" ? "archive" : `${w}h`;
}

/** The tab's three halves (view toggle). */
type DbAuditView = "table" | "sql" | "instance";

const VIEWS: DbAuditView[] = ["table", "sql", "instance"];

/** The workload panel's server-side orderings — label mapping follows the
 *  SQL audit panel's "by …" ToggleButtonGroup. */
const ORDER_LABELS: Record<DbTableOrder, string> = {
  workload: "by workload",
  recent: "by recent",
};

const ORDERS: DbTableOrder[] = ["workload", "recent"];

export default function DbAuditPage() {
  const [windowSel, setWindowSel] = useState<WindowSel>(24);
  // archive mode swaps the STORE (source=archive, hours=0 on the wire —
  // the rollup table has no window); numeric windows read the per-pass
  // audit table's trailing window
  const source: DbAuditSource = windowSel === "archive" ? "archive" : "live";
  const hours = windowSel === "archive" ? 0 : windowSel;
  const [view, setView] = useState<DbAuditView>("table");
  const [order, setOrder] = useState<DbTableOrder>("workload");
  const { data, error, loading, refresh } = useChartData(
    () => fetchDbTables(hours, 500, order, source),
    [hours, order, source],
  );

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const amberCount = useMemo(
    () => rows.filter((r) => r.writeFlag === 1).length,
    [rows],
  );
  const redCount = useMemo(
    () => rows.filter((r) => r.writeFlag === 2).length,
    [rows],
  );
  const partitionCount = useMemo(
    () => rows.reduce((sum, r) => sum + (r.partitionCount ?? 0), 0),
    [rows],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, p: 2 }}>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}
      >
        <Typography variant="h6">DB Workload Audit</Typography>
        <Typography variant="caption" color="text.secondary">
          {data
            ? source === "archive"
              ? `monitor.db_table_audit_archive · lifetime rollup · generated ${fmtDateTimeShanghai(data.generatedAt)}`
              : `monitor.db_table_audit · activity-sparse · generated ${fmtDateTimeShanghai(data.generatedAt)}`
            : "loading…"}
        </Typography>
        {/* Refetch feedback for the header controls (window / order /
            refresh): the spinner runs on every in-flight load, stale
            content below stays visible until the new rows land. */}
        {loading && <CircularProgress size={16} aria-label="loading audit" />}
        <Box sx={{ flexGrow: 1 }} />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v: DbAuditView | null) => {
            if (v != null) setView(v);
          }}
        >
          {VIEWS.map((v) => (
            <ToggleButton key={v} value={v}>
              {v}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {/* Always mounted, hidden outside the table view (space reserved) so
            the header toggles keep their width and position across views. */}
        <ToggleButtonGroup
          size="small"
          exclusive
          value={order}
          onChange={(_, v: DbTableOrder | null) => {
            if (v != null) setOrder(v);
          }}
          sx={{ visibility: view === "table" ? "visible" : "hidden" }}
        >
          {ORDERS.map((o) => (
            <ToggleButton key={o} value={o}>
              {ORDER_LABELS[o]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={windowSel}
          onChange={(_, v: WindowSel | null) => {
            if (v != null) setWindowSel(v);
          }}
        >
          {WINDOWS.map((w) => (
            <ToggleButton key={w} value={w}>
              {windowLabel(w)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Tooltip title="Reload the audit window">
          <span>
            <IconButton size="small" aria-label="refresh" onClick={refresh}>
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {view === "table" && (
        <>
          {error != null && (
            <Alert severity="error">Failed to load the workload audit: {error}</Alert>
          )}

          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            <Chip
              label={
                source === "archive"
                  ? `${rows.length.toLocaleString()} tables · lifetime`
                  : `${rows.length.toLocaleString()} tables active`
              }
              variant="outlined"
            />
            <Chip
              label={
                source === "archive"
                  ? `${partitionCount.toLocaleString()} partitions (live families)`
                  : `${partitionCount.toLocaleString()} partitions`
              }
              variant="outlined"
            />
            <Chip
              label={`${amberCount} amber`}
              variant="outlined"
              color={amberCount > 0 ? "warning" : "default"}
            />
            <Chip
              label={`${redCount} red`}
              variant="outlined"
              color={redCount > 0 ? "error" : "default"}
            />
          </Box>

          <TableWorkloadTable rows={rows} hours={hours} source={source} />

          <AlertsPanel />
        </>
      )}

      {view === "sql" && <SqlAuditPanel />}

      {view === "instance" && <InstanceVitalsPanel hours={hours} />}
    </Box>
  );
}
