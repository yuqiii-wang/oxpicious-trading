/**
 * Monitoring API routes — service process liveness + run audit + the
 * external-resource request audit aggregates.
 *
 *   GET /api/monitor/stream
 *     → heartbeat state of the monitored services (scope + sub_scope per
 *       row, stop tracking: stopReason/isNormalExit/stoppedAt, and the
 *       per-service live_parallel from the heartbeat row + max_parallel
 *       from the static JSON config) plus
 *       the watchdog restart trail; `processes` carries the heartbeat
 *       rows of every OTHER registered pipeline (batch builds /
 *       downloads / analyses / llm runs — last-run state, annotated with
 *       the supposed-run freq/schedule from the service_config/ static
 *       tree; the UI expands each row to its run history via
 *       /stream/runs below).
 *
 *   GET /api/monitor/stream/runs?service=...&limit=10
 *     → run audit history of ONE pipeline process (monitor.service_audit).
 *
 *   POST /api/monitor/health-check
 *     → force-trigger `python -m database.monitoring` (the point-in-time
 *       alert check: replica sync, replay backlog, slot WAL retention,
 *       pg_wal dir size vs max_wal_size, downloads-stream liveness) and
 *       return its [ok] / [ALERT] lines + the health verdict. The UI's
 *       refresh button on the Service Monitor page fires this.
 *
 *   POST /api/monitor/stream-always
 *     → bring the always-on stream up: the idempotent watchdog starter
 *       (scripts/stream_watchdog.sh) then the staggered poller starter
 *       (scripts/stream_always.sh — nohup the five downloads.stream.*
 *       pollers, sleep 1 apart), run inside a Task Scheduler task whose
 *       console session keeps them alive (a transient wsl.exe session's
 *       nohup'd children die with its client, and headless clients don't
 *       hold the distro — the task's pty session does). The route
 *       re-creates the task and triggers it, resolving as soon as both
 *       schtasks calls return. The UI's "Start stream always" button
 *       fires this.
 *
 *   GET /api/monitor/ext-resources/stats?days=30
 *     → every monitor.external_resource_stats row in the trailing window
 *       (grain: service × status_code × date — the per-day aggregation of
 *       the external request audit, computed by python -m
 *       database.external_resource_stats) + the distinct date / service
 *       axes for the trend chart. Serves the External Resources tab's
 *       trend + per-date breakdown; the per-row drill-down to raw audit
 *       rows is /ext-resources/audit below.
 *
 *   GET /api/monitor/ext-resources/audit?service=...&date=YYYY-MM-DD
 *                                      [&status=<code>|none] [&limit=]
 *     → raw monitor.external_resource_audit rows for ONE (service, date)
 *       bucket, optionally narrowed to one status_code bucket ("none" =
 *       the no-response transport-error bucket). resource_type / sub_type
 *       arrive DECODED (label-code contract: decode at the API boundary;
 *       sub_type's domain is selected by resource_type).
 *
 *   GET /api/monitor/ext-resources/configs
 *     → the whole external-request config inventory — per-(service,
 *       request_type) pacing / anti-bot config (sleep cooldown, jitter,
 *       poll cadence, retries/backoff, timeouts, toggles) from the
 *       static JSON tree (database/sql/monitoring/static/
 *       external_request_config/<dotted.service.path>.json via the
 *       shared static-config service). Tiny by construction (one row per
 *       request type per service), so the tab fetches it whole once: the
 *       breakdown table shows each service's config count and expands to
 *       the config rows alongside the raw audit sample. resource_type
 *       arrives DECODED like the audit rows.
 *
 *   GET /api/monitor/src-data-audit?sec_type=index|etf|stock|all&q=&limit=
 *     → the per-security source-data coverage audit
 *       (monitor.src_data_audit — n_days / first_date / last_date per
 *       (sec_type, code), upserted by the classification + ETF builds).
 *       Serves the Source Data tab's filterable table (sec_type dropdown +
 *       code/name search); name is JOINed in from stats.sec_classification.
 *
 * Reads monitor.service_heartbeat / monitor.service_audit /
 * monitor.external_resource_stats / monitor.external_resource_audit /
 * monitor.src_data_audit on
 * the read replica — beats are upserted every ~60s and the stale
 * threshold is 240s, so ordinary replica lag never flips a live service
 * to stale. The watchdog restart trail is merged into service_audit (a
 * restart closes the dead life with a 'watchdog restart: …' exit_reason;
 * recentRestarts is a window over those rows).
 */
import { execFile } from "child_process";
import path from "path";
import { Router, type Request, type Response } from "express";
import { promisify } from "util";
import { getReadPool } from "../services/db.service.js";
import { decodeLabel, encodeLabelParam } from "../services/label-codes.js";
import { runPythonModule } from "../services/py-runner.service.js";
import {
  maxParallelFor,
  readDefaultExtRequestConfig,
  readExternalRequestConfig,
  serviceConfigFor,
  type ExtRequestConfigRow,
} from "../services/static-config.service.js";

const execFileP = promisify(execFile);

const router = Router();

// Mirror of _common/service_heartbeat.py (MONITORED_SERVICES — unique
// service name → scope/sub_scope — plus STALE_AFTER_S); keep in sync. The
// heartbeat table only tells us which services HAVE beaten, not which
// SHOULD, so the expected set lives here.
const WATCHDOG_SERVICE = "database.stream_watchdog";
const STALE_AFTER_S = 240;
const SERVICE_CATALOG: ReadonlyArray<{
  service: string;
  scope: string;
  subScope: string | null;
}> = [
  { service: "downloads.stream.sse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.szse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.bse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.csindex.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.cnindex.price", scope: "downloads", subScope: "stream" },
  // DB health sampler — always-on like the pollers (stream sub-scope).
  { service: "database.db_health", scope: "database", subScope: "stream" },
  { service: WATCHDOG_SERVICE, scope: "database", subScope: "monitoring" },
];

interface BeatRow {
  service: string;
  pid: number;
  started_at: Date;
  beat_at: Date;
  beat_age_s: number;
  scope: string;
  sub_scope: string | null;
  stop_reason: string | null;
  is_normal_exit: boolean | null;
  stopped_at: Date | null;
  live_parallel: number;
}

/** A watchdog restart, read from the merged trail: the dead life's row in
 *  monitor.service_audit, closed with a 'watchdog restart: …' exit_reason
 *  when the watchdog spawned the fresh process. */
interface RestartRow {
  service: string;
  ended_at: Date;
  pid: number;
  exit_reason: string;
  scope: string | null;
  sub_scope: string | null;
}

interface AuditLifeRow {
  id: string;
  service: string;
  scope: string;
  sub_scope: string | null;
  pid: number;
  started_at: Date;
  ended_at: Date | null;
  exit_reason: string | null;
  is_normal_exit: boolean | null;
}

export interface StreamServiceStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  status: "live" | "stale" | "stopped" | "down";
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  restarts24h: number;
  /** monitor.service_heartbeat.live_parallel / the max_parallel cap from
   *  the static JSON config (a registration auto-exits past the cap). */
  liveParallel: number | null;
  maxParallel: number | null;
}

/** Non-stream pipeline process (batch builds / downloads / analyses /
 * llm / live runs): the heartbeat table's LAST-RUN state. "stale" means
 * the beat went quiet with no stop record — died without a record until
 * the watchdog's orphan pass marks it stopped. freq / schedule come from
 * the service_config/ static tree (how often the service is SUPPOSED to
 * run — scripts/*.sh cadence; on_demand for services without their own
 * file). */
export interface ProcessStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number;
  startedAt: string;
  beatAt: string;
  beatAgeS: number;
  status: "running" | "stale" | "stopped";
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  liveParallel: number | null;
  maxParallel: number | null;
  freq: string;
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
}

export interface MonitorStreamResponse {
  success: true;
  generatedAt: string;
  staleAfterS: number;
  watchdog: StreamServiceStatus | null;
  services: StreamServiceStatus[];
  processes: ProcessStatus[];
  recentRestarts: Array<{
    service: string;
    scope: string | null;
    subScope: string | null;
    /** When the watchdog restarted the service = the dead life's ended_at. */
    restartedAt: string;
    /** The pid the restart replaced (the dead life's pid). */
    pid: number;
    reason: string;
  }>;
}

function toStatus(
  expected: { service: string; scope: string; subScope: string | null },
  beat: BeatRow | undefined,
  restarts24h: Map<string, number>,
): StreamServiceStatus {
  if (!beat) {
    return {
      service: expected.service,
      scope: expected.scope,
      subScope: expected.subScope,
      pid: null,
      startedAt: null,
      beatAt: null,
      beatAgeS: null,
      status: "down",
      stopReason: null,
      isNormalExit: null,
      stoppedAt: null,
      restarts24h: restarts24h.get(expected.service) ?? 0,
      liveParallel: 0,
      maxParallel: maxParallelFor(expected.service), // static JSON config
    };
  }
  const status: StreamServiceStatus["status"] =
    beat.stop_reason != null
      ? "stopped"
      : beat.beat_age_s <= STALE_AFTER_S
        ? "live"
        : "stale";
  return {
    service: expected.service,
    scope: beat.scope || expected.scope,
    subScope: beat.sub_scope ?? expected.subScope,
    pid: beat.pid,
    startedAt: beat.started_at.toISOString(),
    beatAt: beat.beat_at.toISOString(),
    beatAgeS: beat.beat_age_s,
    status,
    stopReason: beat.stop_reason,
    isNormalExit: beat.is_normal_exit,
    stoppedAt: beat.stopped_at ? beat.stopped_at.toISOString() : null,
    restarts24h: restarts24h.get(expected.service) ?? 0,
    liveParallel: beat.live_parallel,
    maxParallel: maxParallelFor(expected.service), // static JSON config
  };
}

/** GET /api/monitor/stream/runs?service=...&limit=10 — run audit history
 *  of ONE pipeline process (the pipeline table's expandable rows). Full
 *  per-service history with its own limit — the UI dropped the global
 *  recent-runs table because the shared window crowded each service out
 *  (~50 rows across ~30 registered services ≈ 2 runs each). */
router.get("/stream/runs", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  const limit = Math.min(Math.max(Number(req.query.limit) || 10, 1), 100);
  if (!service) {
    res.status(400).json({ success: false, error: "query param 'service' is required" });
    return;
  }
  try {
    const pool = getReadPool();
    const rows = await pool.query<AuditLifeRow>(
      `SELECT id, service, scope, sub_scope, pid, started_at, ended_at,
              exit_reason, is_normal_exit
       FROM monitor.service_audit
       WHERE service = $1
       ORDER BY started_at DESC
       LIMIT $2`,
      [service, limit],
    );
    res.json({
      success: true,
      service,
      runs: rows.rows.map((r) => ({
        id: r.id,
        service: r.service,
        scope: r.scope,
        subScope: r.sub_scope,
        pid: r.pid,
        startedAt: r.started_at.toISOString(),
        endedAt: r.ended_at ? r.ended_at.toISOString() : null,
        exitReason: r.exit_reason,
        isNormalExit: r.is_normal_exit,
      })),
    });
  } catch (err) {
    console.error("[monitor/stream/runs] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

interface ExtStatRow {
  service: string;
  status_code: number | null;
  date: string;
  request_count: number;
  failed_count: number;
  avg_duration_ms: number | null;
  max_duration_ms: number | null;
}

interface ExtAuditRow {
  id: string;
  resource_type: number;
  sub_type: number;
  url: string;
  status_code: number | null;
  is_failed: boolean;
  failure_reason: string | null;
  duration_ms: number | null;
  created_at: Date;
}

export interface ExtResourceStatEntry {
  service: string;
  statusCode: number | null;
  date: string;
  requestCount: number;
  failedCount: number;
  avgDurationMs: number | null;
  maxDurationMs: number | null;
}

export interface ExtResourceStatsResponse {
  success: true;
  generatedAt: string;
  /** Newest computed_at in the window — when the stats runner last refreshed. */
  computedAt: string | null;
  /** Distinct sorted dates present in the window (trend x axis). */
  dates: string[];
  /** Distinct sorted services present in the window (trend series). */
  services: string[];
  rows: ExtResourceStatEntry[];
}

export interface ExtResourceAuditEntry {
  id: string;
  /** Decoded ext_resource label: http | llm. */
  resourceType: string | null;
  /** Decoded ext_http_source (http rows) / ext_llm_provider (llm rows) label. */
  subType: string | null;
  url: string;
  statusCode: number | null;
  isFailed: boolean;
  failureReason: string | null;
  durationMs: number | null;
  createdAt: string;
}

export interface ExtRequestConfigEntry {
  service: string;
  requestType: string;
  /** Decoded ext_resource label: http | llm. */
  resourceType: string | null;
  sleepSec: number | null;
  sleepJitter: number | null;
  pollIntervalSec: number | null;
  maxRetries: number | null;
  retryBackoffSec: number | null;
  timeoutConnectSec: number | null;
  timeoutReadSec: number | null;
  rotateBrowserProfile: boolean;
  addRandomParam: boolean;
  enableHostTracking: boolean;
  hostBlockCooldownSec: number | null;
  notes: string | null;
  sourceRef: string;
}

export interface ExtRequestConfigsResponse {
  success: true;
  generatedAt: string;
  rows: ExtRequestConfigEntry[];
  /** The default.json fallback rows (repo-wide 60s anti-bot cooldown),
   *  for services without their own file — service is the pseudo-name
   *  "default"; substitute the requesting service client-side. */
  defaultRows: ExtRequestConfigEntry[];
}

/** GET /api/monitor/ext-resources/stats?days=30 — the computed per-day
 *  external-request aggregates (python -m
 *  database.external_resource_stats maintains the table; this endpoint is
 *  a read-only window over it). Small by construction — days × services ×
 *  statuses rows — so the tab fetches the whole window once and pivots
 *  client-side. */
router.get("/ext-resources/stats", async (req: Request, res: Response) => {
  const days = Math.min(Math.max(Number(req.query.days) || 30, 1), 365);
  try {
    const pool = getReadPool();
    const stats = await pool.query<ExtStatRow>(
      `SELECT service, status_code, date, request_count, failed_count,
              avg_duration_ms, max_duration_ms
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ($1::int - 1))
       ORDER BY date, service, status_code NULLS FIRST`,
      [days],
    );
    const computed = await pool.query<{ computed_at: Date | null }>(
      `SELECT max(computed_at) AS computed_at
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ($1::int - 1))`,
      [days],
    );
    const rows: ExtResourceStatEntry[] = stats.rows.map((r) => ({
      service: r.service,
      statusCode: r.status_code,
      date: r.date,
      requestCount: r.request_count,
      failedCount: r.failed_count,
      avgDurationMs: r.avg_duration_ms == null ? null : Number(r.avg_duration_ms),
      maxDurationMs: r.max_duration_ms,
    }));
    const body: ExtResourceStatsResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      computedAt: computed.rows[0]?.computed_at
        ? computed.rows[0].computed_at.toISOString()
        : null,
      dates: [...new Set(rows.map((r) => r.date))].sort(),
      services: [...new Set(rows.map((r) => r.service))].sort(),
      rows,
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ext-resources/stats] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/monitor/ext-resources/audit?service=&date=&status=&limit= —
 *  raw audit rows behind ONE (service, date) aggregate bucket. status
 *  = "none" selects the no-response bucket (status_code IS NULL); a
 *  numeric value narrows to that HTTP status; omitted returns the
 *  service+date rows across all statuses. */
router.get("/ext-resources/audit", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  const date = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const statusRaw = typeof req.query.status === "string" ? req.query.status.trim() : "";
  const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
  if (!service || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    res.status(400).json({
      success: false,
      error: "query params 'service' and 'date' (YYYY-MM-DD) are required",
    });
    return;
  }
  // "none" → status_code IS NULL (transport errors); digits → that status;
  // anything else non-empty is a bad param, not a silent "all".
  let statusClause = "";
  let statusParam: number | null = null;
  if (statusRaw === "none") {
    statusClause = " AND status_code IS NULL";
  } else if (statusRaw !== "") {
    if (!/^\d{1,3}$/.test(statusRaw)) {
      res.status(400).json({
        success: false,
        error: "query param 'status' must be a numeric HTTP status or 'none'",
      });
      return;
    }
    statusParam = Number(statusRaw);
    statusClause = " AND status_code = $3";
  }
  try {
    const pool = getReadPool();
    const params: Array<string | number | number> = [service, date];
    if (statusParam != null) params.push(statusParam);
    params.push(limit);
    const rows = await pool.query<ExtAuditRow>(
      `SELECT id, resource_type, sub_type, url, status_code, is_failed,
              failure_reason, duration_ms, created_at
       FROM monitor.external_resource_audit
       WHERE service = $1 AND date = $2::date${statusClause}
       ORDER BY created_at DESC, id DESC
       LIMIT $${params.length}`,
      params,
    );
    res.json({
      success: true,
      service,
      date,
      entries: rows.rows.map((r) => ({
        id: r.id,
        resourceType: decodeLabel("ext_resource", r.resource_type),
        subType: decodeLabel(
          r.resource_type === 0 ? "ext_http_source" : "ext_llm_provider",
          r.sub_type,
        ),
        url: r.url,
        statusCode: r.status_code,
        isFailed: r.is_failed,
        failureReason: r.failure_reason,
        durationMs: r.duration_ms,
        createdAt: r.created_at.toISOString(),
      })),
    });
  } catch (err) {
    console.error("[monitor/ext-resources/audit] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/monitor/ext-resources/configs — the whole per-(service,
 *  request_type) pacing / anti-bot config inventory, read from the
 *  static JSON tree (database/sql/monitoring/static/
 *  external_request_config/ — the config table's replacement; the
 *  shared static-config service owns the file access). One entry per
 *  request type per service (a service can issue several request kinds
 *  with different cadences), so the table is tiny and fetched whole
 *  once. `defaultRows` carries the default.json fallback (60s cooldown)
 *  for services without their own file. */
router.get("/ext-resources/configs", (_req: Request, res: Response) => {
  try {
    const toEntry = (r: ExtRequestConfigRow): ExtRequestConfigEntry => ({
      service: r.service,
      requestType: r.request_type,
      resourceType: decodeLabel("ext_resource", r.resource_type),
      sleepSec: r.sleep_sec,
      sleepJitter: r.sleep_jitter,
      pollIntervalSec: r.poll_interval_sec,
      maxRetries: r.max_retries,
      retryBackoffSec: r.retry_backoff_sec,
      timeoutConnectSec: r.timeout_connect_sec,
      timeoutReadSec: r.timeout_read_sec,
      rotateBrowserProfile: r.rotate_browser_profile,
      addRandomParam: r.add_random_param,
      enableHostTracking: r.enable_host_tracking,
      hostBlockCooldownSec: r.host_block_cooldown_sec,
      notes: r.notes,
      sourceRef: r.source_ref,
    });
    const body: ExtRequestConfigsResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      rows: readExternalRequestConfig().map(toEntry),
      defaultRows: readDefaultExtRequestConfig().map(toEntry),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ext-resources/configs] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/monitor/src-data-audit?sec_type=index|etf|stock|all&q=&limit=
 *  — the per-security source-data coverage audit
 *  (monitor.src_data_audit: n_days / first_date / last_date per
 *  (sec_type, code), written by the classification + ETF builds).
 *  sec_type filters the SMALLINT label-code column (domain 'sec_type' —
 *  the param is encoded via the label-code contract, rows decode at this
 *  boundary); q narrows by code substring or display name (name JOINed in
 *  from stats.sec_classification — one row per code, primary-parent row
 *  preferred). Ordered sec_type, code for a stable listing; per-type row
 *  counts (decoded keys) ride along for the header. */
interface SrcDataAuditRow {
  sec_type: number;
  code: string;
  exchange: string | null;
  board: number | null;
  n_days: number;
  first_date: string | null;
  last_date: string | null;
  updated_at: Date;
  name: string | null;
}

export interface SrcDataAuditEntry {
  secType: string;
  code: string;
  name: string | null;
  exchange: string | null;
  /** Decoded board label (board label-code domain): MAIN | STAR | GEM |
   *  BSE — stock rows only, null for index/etf. */
  board: string | null;
  nDays: number;
  firstDate: string | null;
  lastDate: string | null;
  updatedAt: string;
}

export interface SrcDataAuditResponse {
  success: true;
  generatedAt: string;
  /** Total rows per sec_type (decoded label keys, unfiltered — the
   *  header's counts). */
  counts: Record<string, number>;
  rows: SrcDataAuditEntry[];
}

router.get("/src-data-audit", async (req: Request, res: Response) => {
  const secTypeRaw = typeof req.query.sec_type === "string" ? req.query.sec_type.trim() : "";
  const q = typeof req.query.q === "string" ? req.query.q.trim() : "";
  // Cap sits above the full universe (~20k rows) so the UI can fetch one
  // sec_type COMPLETE — a "check" table that silently truncates is useless.
  const limit = Math.min(Math.max(Number(req.query.limit) || 20000, 1), 20000);
  // "all" (or omitted) → no sec_type filter; anything else must decode to
  // a 'sec_type' label code — a bad value is a 400, not a silent "all".
  const secTypeCode =
    secTypeRaw === "all" || secTypeRaw === ""
      ? null
      : encodeLabelParam("sec_type", secTypeRaw);
  if (secTypeRaw !== "all" && secTypeRaw !== "" && secTypeCode === null) {
    res.status(400).json({
      success: false,
      error: "query param 'sec_type' must be index | etf | stock | all",
    });
    return;
  }
  try {
    const pool = getReadPool();
    const countsRows = await pool.query<{ sec_type: number; n: string }>(
      `SELECT sec_type, count(*) AS n FROM monitor.src_data_audit GROUP BY sec_type`,
    );
    const counts: Record<string, number> = {};
    for (const r of countsRows.rows) {
      const label = decodeLabel("sec_type", r.sec_type);
      if (label != null) counts[label] = Number(r.n);
    }
    const rows = await pool.query<SrcDataAuditRow>(
      `SELECT a.sec_type, a.code, a.exchange, a.board, a.n_days,
              a.first_date::text AS first_date,
              a.last_date::text  AS last_date,
              a.updated_at, sc.name
       FROM monitor.src_data_audit a
       LEFT JOIN LATERAL (
            SELECT s.name
              FROM stats.sec_classification s
             WHERE s.code = a.code AND s.type = $2::text
             ORDER BY s.parent_index_is_primary DESC
             LIMIT 1
       ) sc ON TRUE
       WHERE ($1::smallint IS NULL OR a.sec_type = $1)
         AND ($3::text = '' OR a.code ILIKE '%' || $3 || '%'
                         OR sc.name ILIKE '%' || $3 || '%')
       ORDER BY a.sec_type, a.code
       LIMIT $4`,
      [secTypeCode, secTypeRaw === "all" || secTypeRaw === "" ? null : secTypeRaw, q, limit],
    );
    const body: SrcDataAuditResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      counts,
      rows: rows.rows.map((r) => ({
        secType: decodeLabel("sec_type", r.sec_type) ?? String(r.sec_type),
        code: r.code,
        name: r.name,
        exchange: r.exchange,
        board: decodeLabel("board", r.board),
        nDays: r.n_days,
        firstDate: r.first_date,
        lastDate: r.last_date,
        updatedAt: r.updated_at.toISOString(),
      })),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/src-data-audit] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** POST /api/monitor/health-check — force-trigger the point-in-time
 *  health check (`python -m database.monitoring`: replica sync + replay
 *  backlog + slot WAL retention + pg_wal dir size vs max_wal_size +
 *  downloads-stream heartbeat liveness) and return its [ok] / [ALERT]
 *  lines. The run also refreshes the check's own heartbeat row, so the
 *  page's stream table picks the forced run up on its next poll. Deduped
 *  by process-id-tag across double clicks / tabs (shared py-runner). */
router.post("/health-check", async (_req: Request, res: Response) => {
  const result = await runPythonModule("database.monitoring", [], {
    processIdTag: "monitor:health-check",
  });
  const lines = result.stdout
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith("[ok]") || l.startsWith("[ALERT]"));
  res.json({
    // success = the check process itself ran (exit code is the health
    // verdict, so a spawn failure is the only non-success here).
    success: result.exitCode !== -1,
    healthy: result.success,
    alreadyRunning: result.already_running === true,
    exitCode: result.exitCode,
    lines,
    stderrTail: result.stderr.slice(-1000),
  });
});

/** POST /api/monitor/stream-always — bring the always-on stream up: the
 *  idempotent supervisor starter (scripts/stream_watchdog.sh) followed by
 *  the staggered poller starter (scripts/stream_always.sh — nohup-spawns
 *  the five downloads.stream.* pollers, sleep 1 apart). The watchdog's
 *  first pass heals live_parallel from pid liveness (a dead poller cannot
 *  release its own slot) and restarts db_health, so this recovers the
 *  full all-dead state; every spawn is max_parallel-guarded, so starting
 *  over a healthy stream is a harmless no-op (duplicates self-exit).
 *
 *  The starters run inside a Task Scheduler task (its console session is
 *  the only spawn that keeps them alive: WSL tears down a session's whole
 *  process tree when its wsl.exe client exits, and a headless/detached
 *  client does not even hold the distro open — 2026-10-11 probes; the
 *  task's pty-backed session does). The route re-creates the task (so a
 *  moved repo or edited script always wins) and triggers it — both
 *  return fast; the services' real liveness is the heartbeat table
 *  (first beats within ~60s). Starter output lands in
 *  logs/<date>/ui_stream_always.log. */
const STREAM_ALWAYS_TASK = "oxpicious-stream-always";
const STREAM_ALWAYS_TASK_CMD = path.resolve(
  import.meta.dirname ?? __dirname,
  "..",
  "..",
  "..",
  "scripts",
  "ui_stream_always.cmd",
);

router.post("/stream-always", async (_req: Request, res: Response) => {
  try {
    await execFileP("schtasks", [
      "/Create",
      "/TN",
      STREAM_ALWAYS_TASK,
      "/TR",
      STREAM_ALWAYS_TASK_CMD,
      "/SC",
      "ONCE",
      "/ST",
      "00:00",
      "/F",
    ]);
    await execFileP("schtasks", ["/Run", "/TN", STREAM_ALWAYS_TASK]);
    res.json({
      success: true,
      message: `scheduled task ${STREAM_ALWAYS_TASK} triggered — first beats within ~60s`,
    });
  } catch (err) {
    res.json({ success: false, message: String(err) });
  }
});

/** GET /api/monitor/stream */
router.get("/stream", async (_req: Request, res: Response) => {
  try {
    const pool = getReadPool();
    const beats = await pool.query<BeatRow>(
      `SELECT h.service, h.pid, h.started_at, h.beat_at,
              EXTRACT(EPOCH FROM (now() - h.beat_at))::int AS beat_age_s,
              h.scope, h.sub_scope, h.stop_reason, h.is_normal_exit,
              h.stopped_at, h.live_parallel
       FROM monitor.service_heartbeat h
       ORDER BY h.service`,
    );
    // The watchdog restart trail, merged into service_audit: the dead
    // life's row closed with a 'watchdog restart: …' exit_reason.
    const restarts = await pool.query<RestartRow>(
      `SELECT service, ended_at, pid, exit_reason, scope, sub_scope
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
       ORDER BY ended_at DESC
       LIMIT 20`,
    );
    const counts = await pool.query<{ service: string; restarts_24h: string }>(
      `SELECT service, count(*) AS restarts_24h
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
         AND ended_at > now() - interval '24 hours'
       GROUP BY service`,
    );
    const restarts24h = new Map(
      counts.rows.map((r) => [r.service, Number(r.restarts_24h)]),
    );
    const byService = new Map(beats.rows.map((r) => [r.service, r]));

    const statuses = SERVICE_CATALOG.map((c) =>
      toStatus(c, byService.get(c.service), restarts24h),
    );

    // Batch pipelines: heartbeat rows outside the always-on catalog —
    // their LAST-RUN state (run history is /stream/runs on demand).
    const catalog = new Set(SERVICE_CATALOG.map((c) => c.service));
    const processes: ProcessStatus[] = beats.rows
      .filter((r) => !catalog.has(r.service))
      .map((r): ProcessStatus => {
        const status: ProcessStatus["status"] =
          r.stop_reason != null
            ? "stopped"
            : r.beat_age_s <= STALE_AFTER_S
              ? "running"
              : "stale";
        // Supposed-run cadence from the service_config/ static tree (the
        // default.json fallback supplies on_demand for unlisted services).
        const cfg = serviceConfigFor(r.service);
        return {
          service: r.service,
          scope: r.scope || "unknown",
          subScope: r.sub_scope,
          pid: r.pid,
          startedAt: r.started_at.toISOString(),
          beatAt: r.beat_at.toISOString(),
          beatAgeS: r.beat_age_s,
          status,
          stopReason: r.stop_reason,
          isNormalExit: r.is_normal_exit,
          stoppedAt: r.stopped_at ? r.stopped_at.toISOString() : null,
          liveParallel: r.live_parallel,
          maxParallel: maxParallelFor(r.service), // static JSON config
          freq: cfg.freq,
          schedule: cfg.schedule,
          notes: cfg.notes,
          sourceRef: cfg.source_ref,
        };
      })
      .sort((a, b) =>
        a.status === b.status
          ? a.service.localeCompare(b.service)
          : a.status === "running"
            ? -1
            : b.status === "running"
              ? 1
              : 0,
      );

    const body: MonitorStreamResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      staleAfterS: STALE_AFTER_S,
      watchdog:
        statuses.find((s) => s.service === WATCHDOG_SERVICE) ?? null,
      services: statuses.filter((s) => s.service !== WATCHDOG_SERVICE),
      processes,
      recentRestarts: restarts.rows.map((r) => ({
        service: r.service,
        scope: r.scope,
        subScope: r.sub_scope,
        restartedAt: r.ended_at.toISOString(),
        pid: r.pid,
        reason: r.exit_reason,
      })),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/stream] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
