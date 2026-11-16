/**
 * GET /db/tables + GET /db/partitions + GET /db/trend + GET /db/alerts +
 * GET /db/instances — the DB tab's per-table workload audit (main tables
 * only; partitions load lazily per family; source=live|archive — the
 * per-pass audit vs the computed daily rollup's lifetime aggregate), the
 * write-heavy alert trail and the instance vitals panel. Full route
 * docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, intParam, wrapAsync } from "../../lib/http.js";
import { getReadPool } from "../../services/infra/db.service.js";
import { renderSqlView, sqlFrag } from "@api/lib/sql-views";
import { decodeLabel } from "../../services/infra/label-codes.js";

const router = Router();

// ---------------------------------------------------------------------------
//  source param — which store the workload endpoints read: live = the
//  per-pass audit (monitor.db_table_audit, trailing hours window, ~30d
//  retention) vs archive = the computed daily rollup
//  (monitor.db_table_audit_archive) collapsed to a LIFETIME aggregate
//  per table (no window — the archive IS the retention, database/
//  db_table_audit_archive.py). Whitelisted; anything else 400s. The
//  archive views emit the live views' output columns 1:1 (window_s
//  constant NULL), so the order whitelist and the row mapping below are
//  shared verbatim; hours echoes 0 in archive mode (no window semantics).
// ---------------------------------------------------------------------------

type DbSource = "live" | "archive";

function sourceParam(req: Request): DbSource {
  const source = String(req.query.source ?? "live");
  if (source !== "live" && source !== "archive") {
    throw new HttpError(400, "INVALID_PARAM", "source must be live or archive");
  }
  return source;
}

// ---------------------------------------------------------------------------
//  Per-table workload — GET /db/tables?hours=24&order=workload&limit=500
//                                   [&source=live|archive]
//  The DB tab's main panel: MAIN TABLES ONLY — one row per non-partition
//  table, with each partition FAMILY folded into its parent row (write /
//  read rates and delta counts summed across the partitions, flag the max
//  family severity, stamps the latest of the family — see
//  db_tables_latest.sql). The partitions themselves load lazily via
//  GET /db/partitions when a row is expanded. Rows are ACTIVITY-SPARSE
//  (idle tables write nothing — absence = idle), so "latest row in
//  window" = "last active pass". order maps through a strict whitelist to
//  the ORDER BY fragment (workload heaviness | recent activity), applied
//  BEFORE the limit so the slice is the top of the chosen ordering.
//  source=archive swaps the store to the computed daily rollup
//  (monitor.db_table_audit_archive) collapsed to a LIFETIME aggregate per
//  table (db_tables_archive.sql): day sums of ins/upd/del added up, peak
//  write/read rates the worst day's, write_flag the worst-ever verdict,
//  live/dead tup and size the latest archived day's end-of-day snapshots,
//  stamps the most recent active day — no window (hours echoes 0), the
//  family folding unchanged, dropped tables' archive rows kept.
//  write_flag arrives DECODED (label-code contract:
//  decode at the API boundary; null = not evaluated — baseline pass /
//  replica-only row).
// ---------------------------------------------------------------------------

interface DbTableRow {
  table_schema: string | null;
  table_name: string | null;
  write_checked_at: Date | null;
  window_s: number | null;
  write_rate: string | null;
  ins_rows: string | number | null;
  upd_rows: string | number | null;
  del_rows: string | number | null;
  read_checked_at: Date | null;
  read_rate: string | null;
  n_live_tup: string | number | null;
  n_dead_tup: string | number | null;
  table_size_bytes: string | number | null;
  write_flag: number | null;
  partition_count: string | number | null;
}

export interface DbTableEntry {
  schema: string;
  table: string;
  /** The primary-side row's stamp — the table's last WRITE-active pass. */
  writeCheckedAt: string | null;
  windowS: number | null;
  /** (ins+upd+del) rows/s over the window — the write-heavy metric. */
  writeRate: number | null;
  insRows: number | null;
  updRows: number | null;
  delRows: number | null;
  /** The replica-side row's stamp — the table's last READ-active pass. */
  readCheckedAt: string | null;
  /** scans/s of the effective read load (replica side when the API read
   *  it, else the primary's own; summed over a partition family). */
  readRate: number | null;
  nLiveTup: number | null;
  nDeadTup: number | null;
  sizeBytes: number | null;
  /** db_write_flag code: 0 ok | 1 amber | 2 red; null = not evaluated. */
  writeFlag: number | null;
  writeFlagLabel: string | null;
  /** Live partition children of this table (0 = not partitioned) — the
   *  expand-to-partitions affordance; the family's own row above. */
  partitionCount: number | null;
}

export interface DbTablesResponse {
  success: true;
  generatedAt: string;
  hours: number;
  order: string;
  source: DbSource;
  rows: DbTableEntry[];
}

export interface DbPartitionsResponse {
  success: true;
  generatedAt: string;
  schema: string;
  table: string;
  hours: number;
  source: DbSource;
  rows: DbTableEntry[];
}

/** order param -> ORDER BY fragment (whitelist; anything else 400s).
 *  workload = total write+read heaviness (the panel default), ordered by
 *  the effective read rate the API surfaces (replica side, falling back
 *  to the primary's own — what the Reads column shows); recent = the
 *  later of the two last-active stamps. Fragments run over the view's
 *  outer subquery alias f (its columns are the aggregated family row) —
 *  SHARED by both sources: the archive view exposes the same f.* columns
 *  (its rates are lifetime peaks, its stamps the most recent active day);
 *  the COALESCEs keep GREATEST null-safe under either NULL semantics (a
 *  missing stamp falls back to its sibling; both null -> NULLS LAST). */
const TABLE_ORDER_EXPR: Record<string, string> = {
  workload: `(COALESCE(f.write_rate, 0) + COALESCE(f.read_rate, 0)) DESC NULLS LAST, f.write_rate DESC NULLS LAST, f.read_rate DESC NULLS LAST, f.table_schema NULLS LAST, f.table_name NULLS LAST`,
  recent: `GREATEST(COALESCE(f.write_checked_at, f.read_checked_at), COALESCE(f.read_checked_at, f.write_checked_at)) DESC NULLS LAST, f.table_schema NULLS LAST, f.table_name NULLS LAST`,
};

/** DB audit row -> API entry (shared by /db/tables and /db/partitions;
 *  the latter just never carries a partition_count). */
function toDbTableEntry(r: DbTableRow): DbTableEntry {
  return {
    schema: r.table_schema ?? "",
    table: r.table_name ?? "",
    writeCheckedAt: r.write_checked_at?.toISOString() ?? null,
    windowS: r.window_s,
    writeRate: r.write_rate == null ? null : Number(r.write_rate),
    insRows: r.ins_rows == null ? null : Number(r.ins_rows),
    updRows: r.upd_rows == null ? null : Number(r.upd_rows),
    delRows: r.del_rows == null ? null : Number(r.del_rows),
    readCheckedAt: r.read_checked_at?.toISOString() ?? null,
    readRate: r.read_rate == null ? null : Number(r.read_rate),
    nLiveTup: r.n_live_tup == null ? null : Number(r.n_live_tup),
    nDeadTup: r.n_dead_tup == null ? null : Number(r.n_dead_tup),
    sizeBytes: r.table_size_bytes == null ? null : Number(r.table_size_bytes),
    writeFlag: r.write_flag,
    writeFlagLabel: decodeLabel("db_write_flag", r.write_flag),
    partitionCount: r.partition_count == null ? null : Number(r.partition_count),
  };
}

router.get("/db/tables", wrapAsync(async (req: Request, res: Response) => {
  const source = sourceParam(req);
  // archive mode has NO window (hours echoes 0); live hours=0 = no lower
  // bound — the audit's full retained history (the sampler's
  // --retain-days prunes the table; 0 bypasses the window)
  const hours = source === "archive" ? 0 : intParam(req, "hours", { default: 24, clamp: [0, 168] });
  const order = String(req.query.order ?? "workload");
  const orderExpr = TABLE_ORDER_EXPR[order];
  if (orderExpr === undefined) {
    throw new HttpError(400, "INVALID_PARAM", `order must be one of ${Object.keys(TABLE_ORDER_EXPR).join("|")}`);
  }
  const limit = intParam(req, "limit", { default: 500, clamp: [1, 2000] });
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    source === "archive"
      ? "data_viz/api/routes/monitor/db_tables_archive"
      : "data_viz/api/routes/monitor/db_tables_latest",
    // the archive view has no hours slot (no window semantics)
    source === "archive"
      ? { order_expr: sqlFrag(orderExpr), limit }
      : { hours, order_expr: sqlFrag(orderExpr), limit },
  );
  const agg = await pool.query<DbTableRow>(sql, params);
  const rows: DbTableEntry[] = agg.rows.map(toDbTableEntry);
  const body: DbTablesResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    hours,
    order,
    source,
    rows,
  };
  res.json(body);
}));

// ---------------------------------------------------------------------------
//  One table's partitions — GET /db/partitions?schema=stats&table=...&hours=24
//                                              [&source=live|archive]
//  The lazy expansion behind a partitioned-family row in /db/tables: the
//  live partition children of ONE parent, every partition present
//  (structure) with its LATEST audit row in the window when active —
//  NULL metrics = idle in the window (activity-sparse, like the panel).
//  source=archive swaps to the rollup table's LIFETIME aggregate per
//  partition (db_table_partitions_archive.sql — no window, hours echoes
//  0). schema/table are compared as VALUES (not interpolated
//  identifiers), so the view slots bind them as ordinary parameters.
// ---------------------------------------------------------------------------

router.get("/db/partitions", wrapAsync(async (req: Request, res: Response) => {
  const schema = String(req.query.schema ?? "");
  const table = String(req.query.table ?? "");
  const source = sourceParam(req);
  // archive mode has NO window (hours echoes 0); live hours=0 = archive-
  // style full retained history, like /db/tables
  const hours = source === "archive" ? 0 : intParam(req, "hours", { default: 24, clamp: [0, 168] });
  if (!/^[a-z_][a-z0-9_]*$/.test(schema) || !/^[a-z_][a-z0-9_]*$/.test(table)) {
    throw new HttpError(400, "INVALID_PARAM", "bad schema or table name");
  }
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    source === "archive"
      ? "data_viz/api/routes/monitor/db_table_partitions_archive"
      : "data_viz/api/routes/monitor/db_table_partitions",
    // the archive view has no hours slot (no window semantics)
    source === "archive" ? { schema, table } : { schema, table, hours },
  );
  const agg = await pool.query<DbTableRow>(sql, params);
  const rows: DbTableEntry[] = agg.rows.map(toDbTableEntry);
  const body: DbPartitionsResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    schema,
    table,
    hours,
    source,
    rows,
  };
  res.json(body);
}));

// ---------------------------------------------------------------------------
//  One table's write trend — GET /db/trend?schema=monitor&table=db_health&hours=6
//                                        [&source=live|archive]
//  The DB tab's drill-down chart: write/read rates + delta counts + the
//  write_flag verdict per ACTIVE pass on the primary over a trailing
//  window (activity-sparse rows — idle gaps have no points). source=
//  archive swaps to the rollup table's per-GMT-day series
//  (db_table_trend_archive.sql — one point per archived day the table was
//  active in, over the FULL archive history, no window; the point carries
//  the GMT operational day as `date` since the browser-local calendar
//  date of checked_at can skew a day off the UTC-day grain). FAMILY-AWARE:
//  a partitioned PARENT's passes aggregate its whole family (the parent's
//  own write-through rows are idle zeros — see db_table_trend.sql); the
//  response flags that as family=true so the chart captions it. A plain
//  table or a partition is its own family of one. schema/table are
//  compared as VALUES (not interpolated identifiers), so the view slots
//  bind them as ordinary parameters.
// ---------------------------------------------------------------------------

interface DbTrendRow {
  checked_at: Date;
  /** The GMT operational day — archive points only (null on live rows). */
  date: string | null;
  window_s: number | null;
  write_rate: string | null;
  read_rate: string | null;
  ins_rows: string | number | null;
  upd_rows: string | number | null;
  del_rows: string | number | null;
  write_flag: number | null;
  is_family: boolean;
}

export interface DbTrendPoint {
  at: string;
  /** The GMT operational day — set on archive points (the daily rollup's
   *  grain), null on live per-pass points. */
  date: string | null;
  windowS: number | null;
  writeRate: number | null;
  readRate: number | null;
  insRows: number | null;
  updRows: number | null;
  delRows: number | null;
  writeFlag: number | null;
}

export interface DbTrendResponse {
  success: true;
  generatedAt: string;
  schema: string;
  table: string;
  hours: number;
  source: DbSource;
  /** true = points are the partition-family aggregate of a partitioned
   *  parent (false = a plain table's / a partition's own rows). */
  family: boolean;
  points: DbTrendPoint[];
}

router.get("/db/trend", wrapAsync(async (req: Request, res: Response) => {
  const schema = String(req.query.schema ?? "");
  const table = String(req.query.table ?? "");
  const source = sourceParam(req);
  // archive mode has NO window (hours echoes 0); live hours=0 = archive-
  // style full retained history, like /db/tables
  const hours = source === "archive" ? 0 : intParam(req, "hours", { default: 6, clamp: [0, 168] });
  if (!/^[a-z_][a-z0-9_]*$/.test(schema) || !/^[a-z_][a-z0-9_]*$/.test(table)) {
    throw new HttpError(400, "INVALID_PARAM", "bad schema or table name");
  }
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    source === "archive"
      ? "data_viz/api/routes/monitor/db_table_trend_archive"
      : "data_viz/api/routes/monitor/db_table_trend",
    // the archive view has no hours slot (no window semantics)
    source === "archive" ? { schema, table } : { schema, table, hours },
  );
  const agg = await pool.query<DbTrendRow>(sql, params);
  const points: DbTrendPoint[] = agg.rows.map((r) => ({
    at: r.checked_at.toISOString(),
    date: r.date ?? null,
    windowS: r.window_s,
    writeRate: r.write_rate == null ? null : Number(r.write_rate),
    readRate: r.read_rate == null ? null : Number(r.read_rate),
    insRows: r.ins_rows == null ? null : Number(r.ins_rows),
    updRows: r.upd_rows == null ? null : Number(r.upd_rows),
    delRows: r.del_rows == null ? null : Number(r.del_rows),
    writeFlag: r.write_flag,
  }));
  const body: DbTrendResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    schema,
    table,
    hours,
    source,
    family: agg.rows[0]?.is_family ?? false,
    points,
  };
  res.json(body);
}));

// ---------------------------------------------------------------------------
//  Write-heavy alert trail — GET /db/alerts?limit=50
//  The monitor log behind the flags: monitor.db_table_alert's STATE
//  TRANSITIONS (raise / downgrade / clear), newest first — severity
//  arrives DECODED like write_flag above (0 ok = a clear).
// ---------------------------------------------------------------------------

interface DbAlertRow {
  id: string | number;
  raised_at: Date;
  table_schema: string;
  table_name: string;
  severity: number;
  write_rate: string;
  threshold_rate: string;
  window_s: number;
  message: string;
}

export interface DbAlertEntry {
  id: number;
  raisedAt: string;
  schema: string;
  table: string;
  severity: number;
  severityLabel: string;
  writeRate: number;
  thresholdRate: number;
  windowS: number;
  message: string;
}

export interface DbAlertsResponse {
  success: true;
  generatedAt: string;
  rows: DbAlertEntry[];
}

router.get("/db/alerts", wrapAsync(async (req: Request, res: Response) => {
  const limit = intParam(req, "limit", { default: 50, clamp: [1, 500] });
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    "data_viz/api/routes/monitor/db_alerts",
    { limit },
  );
  const agg = await pool.query<DbAlertRow>(sql, params);
  const rows: DbAlertEntry[] = agg.rows.map((r) => ({
    id: Number(r.id),
    raisedAt: r.raised_at.toISOString(),
    schema: r.table_schema,
    table: r.table_name,
    severity: r.severity,
    severityLabel: decodeLabel("db_write_flag", r.severity) ?? "ok",
    writeRate: Number(r.write_rate),
    thresholdRate: Number(r.threshold_rate),
    windowS: r.window_s,
    message: r.message,
  }));
  const body: DbAlertsResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    rows,
  };
  res.json(body);
}));

// ---------------------------------------------------------------------------
//  SQL audit — GET /db/sql?order=total_ms&limit=100&q=
//  The statement-level half of the DB tab: monitor.sql_audit rows — one
//  per DISTINCT statement executed on the cluster (primary + replica,
//  every client), keyed by sha256 of pg_stat_statements' normalized text
//  (literals -> $n), with delta-accumulated call/time/row totals by
//  database/sql_audit.py. order maps through a strict whitelist to the
//  ORDER BY fragment (values only, never user text verbatim); q narrows
//  to a text substring or a hash prefix.
// ---------------------------------------------------------------------------

/** order param -> ORDER BY fragment (whitelist; anything else 400s). */
const SQL_ORDER_EXPR: Record<string, string> = {
  total_ms: "total_exec_ms DESC",
  calls: "call_count DESC",
  recent: "last_active_at DESC",
  avg_ms: "avg_exec_ms DESC",
};

interface DbSqlRow {
  sql_hash: string;
  sql_text: string;
  queryid: string | number | null;
  roles: string;
  instances: string;
  call_count: string | number;
  total_exec_ms: string;
  avg_exec_ms: string;
  max_exec_ms: string;
  rows_total: string | number;
  first_seen_at: Date;
  last_active_at: Date;
}

export interface DbSqlEntry {
  /** sha256 hex of the normalized statement text — the identity. */
  sqlHash: string;
  sqlText: string;
  queryid: string | null;
  roles: string[];
  instances: string[];
  callCount: number;
  totalExecMs: number;
  avgExecMs: number;
  maxExecMs: number;
  rowsTotal: number;
  firstSeenAt: string;
  lastActiveAt: string;
}

export interface DbSqlResponse {
  success: true;
  generatedAt: string;
  order: string;
  rows: DbSqlEntry[];
}

router.get("/db/sql", wrapAsync(async (req: Request, res: Response) => {
  const order = String(req.query.order ?? "total_ms");
  const expr = SQL_ORDER_EXPR[order];
  if (expr === undefined) {
    throw new HttpError(400, "INVALID_PARAM", `order must be one of ${Object.keys(SQL_ORDER_EXPR).join("|")}`);
  }
  const limit = intParam(req, "limit", { default: 100, clamp: [1, 500] });
  const q = String(req.query.q ?? "");
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    "data_viz/api/routes/monitor/db_sql_rows",
    { q, order_expr: sqlFrag(expr), limit },
  );
  const agg = await pool.query<DbSqlRow>(sql, params);
  const rows: DbSqlEntry[] = agg.rows.map((r) => ({
    sqlHash: r.sql_hash,
    sqlText: r.sql_text,
    queryid: r.queryid == null ? null : String(r.queryid),
    roles: r.roles.split(","),
    instances: r.instances.split(","),
    callCount: Number(r.call_count),
    totalExecMs: Number(r.total_exec_ms),
    avgExecMs: Number(r.avg_exec_ms),
    maxExecMs: Number(r.max_exec_ms),
    rowsTotal: Number(r.rows_total),
    firstSeenAt: r.first_seen_at.toISOString(),
    lastActiveAt: r.last_active_at.toISOString(),
  }));
  const body: DbSqlResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    order,
    rows,
  };
  res.json(body);
}));

// ---------------------------------------------------------------------------
//  Instance vitals — GET /db/instances?hours=24
//  The panel beneath the per-table audit: the LATEST monitor.db_health
//  row per instance (the 60s sampler database/db_health.py — reachability,
//  latency, connections, cache hit, WAL shipping state + replay backlog,
//  pg_wal size, container CPU/mem) plus the trailing connections /
//  cache-hit / backlog trend. instance + replication_state arrive
//  DECODED (domains db_instance / db_replication_state).
// ---------------------------------------------------------------------------

interface DbInstanceRow {
  instance: number;
  checked_at: Date;
  healthy: boolean;
  connect_ms: number | null;
  error: string | null;
  uptime_s: string | number | null;
  active_connections: number | null;
  db_size_bytes: string | number | null;
  cache_hit_ratio: string | null;
  replication_state: number | null;
  replay_backlog_bytes: string | number | null;
  wal_dir_bytes: string | number | null;
  cpu_pct: string | null;
  mem_pct: string | null;
}

interface DbInstanceTrendRow {
  instance: number;
  checked_at: Date;
  active_connections: number | null;
  cache_hit_ratio: string | null;
  replay_backlog_bytes: string | number | null;
}

export interface DbInstanceVitals {
  instance: number;
  instanceLabel: string;
  checkedAt: string;
  healthy: boolean;
  connectMs: number | null;
  error: string | null;
  uptimeS: number | null;
  activeConnections: number | null;
  dbSizeBytes: number | null;
  cacheHitRatio: number | null;
  replicationState: number | null;
  replicationStateLabel: string | null;
  replayBacklogBytes: number | null;
  walDirBytes: number | null;
  cpuPct: number | null;
  memPct: number | null;
}

export interface DbInstanceTrendPoint {
  instance: number;
  at: string;
  activeConnections: number | null;
  cacheHitRatio: number | null;
  replayBacklogBytes: number | null;
}

export interface DbInstancesResponse {
  success: true;
  generatedAt: string;
  latest: DbInstanceVitals[];
  trend: DbInstanceTrendPoint[];
}

router.get("/db/instances", wrapAsync(async (req: Request, res: Response) => {
  // hours=0 = archive (full retained db_health history), like /db/tables
  const hours = intParam(req, "hours", { default: 24, clamp: [0, 168] });
  const pool = getReadPool();
  const latestView = renderSqlView(
    "data_viz/api/routes/monitor/db_instances_latest",
    {},
  );
  const trendView = renderSqlView(
    "data_viz/api/routes/monitor/db_instances_trend",
    { hours },
  );
  const [latestAgg, trendAgg] = await Promise.all([
    pool.query<DbInstanceRow>(latestView.sql, latestView.params),
    pool.query<DbInstanceTrendRow>(trendView.sql, trendView.params),
  ]);
  const latest: DbInstanceVitals[] = latestAgg.rows.map((r) => ({
    instance: r.instance,
    instanceLabel: decodeLabel("db_instance", r.instance) ?? "primary",
    checkedAt: r.checked_at.toISOString(),
    healthy: r.healthy,
    connectMs: r.connect_ms,
    error: r.error,
    uptimeS: r.uptime_s == null ? null : Number(r.uptime_s),
    activeConnections: r.active_connections,
    dbSizeBytes: r.db_size_bytes == null ? null : Number(r.db_size_bytes),
    cacheHitRatio: r.cache_hit_ratio == null ? null : Number(r.cache_hit_ratio),
    replicationState: r.replication_state,
    replicationStateLabel: decodeLabel("db_replication_state", r.replication_state),
    replayBacklogBytes:
      r.replay_backlog_bytes == null ? null : Number(r.replay_backlog_bytes),
    walDirBytes: r.wal_dir_bytes == null ? null : Number(r.wal_dir_bytes),
    cpuPct: r.cpu_pct == null ? null : Number(r.cpu_pct),
    memPct: r.mem_pct == null ? null : Number(r.mem_pct),
  }));
  const trend: DbInstanceTrendPoint[] = trendAgg.rows.map((r) => ({
    instance: r.instance,
    at: r.checked_at.toISOString(),
    activeConnections: r.active_connections,
    cacheHitRatio: r.cache_hit_ratio == null ? null : Number(r.cache_hit_ratio),
    replayBacklogBytes:
      r.replay_backlog_bytes == null ? null : Number(r.replay_backlog_bytes),
  }));
  const body: DbInstancesResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    latest,
    trend,
  };
  res.json(body);
}));

export default router;
