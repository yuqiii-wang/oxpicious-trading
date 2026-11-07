/**
 * /ext-resources/* — the external-request stats window, the raw audit
 * rows behind one bucket, the stats refresh (+ its running-state), and
 * the static request-config inventory.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getReadPool,
} from "../../services/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import { decodeLabel } from "../../services/label-codes.js";
import {
  getPythonProcessStatus,
  runPythonModule,
} from "../../services/py-runner.service.js";
import {
  readDefaultExtRequestConfig,
  readExternalRequestConfig,
  type ExtRequestConfigRow,
} from "../../services/static-config.service.js";

const router = Router();

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
  /** Playwright flows' post-navigation settle wait (null = the flow keeps
   *  its own behavior). */
  settleSleepSec: number | null;
  /** The row's endpoint inventory {role: url} (null = none). */
  urls: Record<string, string> | null;
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
    const statsView = renderSqlView(
      "data_viz/api/routes/monitor/ext_stats_rows",
      { days },
    );
    const computedView = renderSqlView(
      "data_viz/api/routes/monitor/ext_stats_computed_at",
      { days },
    );
    const stats = await pool.query<ExtStatRow>(statsView.sql, statsView.params);
    const computed = await pool.query<{ computed_at: Date | null }>(
      computedView.sql,
      computedView.params,
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
  // anything else non-empty is a bad param, not a silent "all". Each shape
  // is its own sql view — the conditional assembly picks the view name.
  let statusParam: number | null = null;
  if (statusRaw === "none") {
    // no-op — the view narrows to IS NULL
  } else if (statusRaw !== "") {
    if (!/^\d{1,3}$/.test(statusRaw)) {
      res.status(400).json({
        success: false,
        error: "query param 'status' must be a numeric HTTP status or 'none'",
      });
      return;
    }
    statusParam = Number(statusRaw);
  }
  try {
    const pool = getReadPool();
    const view =
      statusRaw === "none"
        ? "data_viz/api/routes/monitor/ext_audit_rows_none"
        : statusParam != null
          ? "data_viz/api/routes/monitor/ext_audit_rows_status"
          : "data_viz/api/routes/monitor/ext_audit_rows";
    const rendered = renderSqlView(
      view,
      statusParam != null
        ? { service, date, status_code: statusParam, limit }
        : { service, date, limit },
    );
    const rows = await pool.query<ExtAuditRow>(rendered.sql, rendered.params);
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

/** Process-id-tag of the ext-resource stats refresh run — one fixed tag:
 *  there is exactly one logical refresh (whole-table incremental), so
 *  double clicks / tabs dedupe onto it. */
const EXT_RES_STATS_TAG = "monitor:ext-res-stats";

/** POST /api/monitor/ext-resources/refresh — re-run the per-day stats
 *  aggregation (python -m database.external_resource_stats) so the
 *  trailing window picks up the audit rows written since the last run.
 *  The stats table is COMPUTED — the stats endpoint above never
 *  aggregates on read — so a day appears only after this has run. The
 *  incremental run rebuilds just the stale dates and exits in seconds. */
router.post("/ext-resources/refresh", async (_req: Request, res: Response) => {
  const result = await runPythonModule("database.external_resource_stats", [], {
    processIdTag: EXT_RES_STATS_TAG,
  });
  // The runner prints "done — N date(s) rebuilt" (0 = already current).
  const rebuilt = /(\d+) date\(s\) rebuilt/.exec(result.stdout);
  res.json({
    success: result.success,
    alreadyRunning: result.already_running === true,
    exitCode: result.exitCode,
    rebuiltDates: rebuilt ? Number(rebuilt[1]) : null,
    stderrTail: result.stderr.slice(-1000),
  });
});

/** GET /api/monitor/ext-resources/refresh/status — running-state of the
 *  stats refresh tag, polled on mount so a page refresh mid-run puts the
 *  button straight back into its spinning state. */
router.get("/ext-resources/refresh/status", (_req: Request, res: Response) => {
  res.json({
    running: getPythonProcessStatus([EXT_RES_STATS_TAG])[EXT_RES_STATS_TAG] ?? false,
  });
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
      settleSleepSec: r.settle_sleep_sec ?? null,
      urls: r.urls ?? null,
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

export default router;
