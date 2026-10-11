/**
 * API client for the stream monitor page (/api/monitor).
 */
import { fetchJson } from "./_cache";

export type StreamServiceState = "live" | "stale" | "stopped" | "down";

export interface StreamServiceStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  status: StreamServiceState;
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  restarts24h: number;
  /** monitor.service_heartbeat.live_parallel / the max_parallel cap from
   *  the static JSON config (a registration auto-exits past the cap). */
  liveParallel: number | null;
  maxParallel: number | null;
}

/** Batch pipeline process — LAST-RUN state from the heartbeat table. */
export type ProcessState = "running" | "stale" | "stopped";

export interface ProcessStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number;
  startedAt: string;
  beatAt: string;
  beatAgeS: number;
  status: ProcessState;
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  liveParallel: number | null;
  maxParallel: number | null;
  /** Supposed-run cadence from the service_config/ static tree
   *  (database/sql/monitoring/static/service_config/), mirrored from
   *  scripts/*.sh; "on_demand" for services without their own file. */
  freq: string;
  /** Human-readable timing detail from the same tree entry. */
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
}

/** One watchdog restart, from the trail merged into monitor.service_audit:
 *  the dead life's row closed with a 'watchdog restart: …' exit_reason. */
export interface StreamRestartEntry {
  service: string;
  scope: string | null;
  subScope: string | null;
  /** When the watchdog restarted the service = the dead life's ended_at. */
  restartedAt: string;
  /** The pid the restart replaced (the dead life's pid). */
  pid: number;
  reason: string;
}

export interface ServiceAuditEntry {
  id: string;
  service: string;
  scope: string;
  subScope: string | null;
  pid: number;
  startedAt: string;
  endedAt: string | null;
  exitReason: string | null;
  isNormalExit: boolean | null;
}

export interface MonitorStreamResponse {
  success: true;
  generatedAt: string;
  staleAfterS: number;
  watchdog: StreamServiceStatus | null;
  services: StreamServiceStatus[];
  processes: ProcessStatus[];
  recentRestarts: StreamRestartEntry[];
}

export interface ProcessRunsResponse {
  success: true;
  service: string;
  runs: ServiceAuditEntry[];
}

/** Heartbeat state of all monitored services + the watchdog, plus the
 * batch pipeline processes' last-run state and the run audit trail. */
export function fetchStreamMonitor(): Promise<MonitorStreamResponse> {
  return fetchJson<MonitorStreamResponse>("/api/monitor/stream");
}

/** Run audit history of ONE pipeline process — fetched on demand when a
 * pipeline-process row is expanded. Append-only history, so the shared
 * client cache's 10-min TTL window is fine. */
export function fetchProcessRuns(
  service: string,
  limit = 10,
): Promise<ServiceAuditEntry[]> {
  return fetchJson<ProcessRunsResponse>(
    `/api/monitor/stream/runs?service=${encodeURIComponent(service)}&limit=${limit}`,
  ).then((r) => r.runs);
}

// ---------------------------------------------------------------------------
// Forced health check — POST /api/monitor/health-check
// ---------------------------------------------------------------------------

/** Result of the forced `python -m database.monitoring` health check.
 * `success` = the check process ran at all; `healthy` = its exit-code
 * verdict (0 = no alerts). `lines` carries the [ok] / [ALERT] output. */
export interface HealthCheckResponse {
  success: boolean;
  healthy: boolean;
  alreadyRunning: boolean;
  exitCode: number;
  lines: string[];
  stderrTail: string;
}

/** Force-trigger the point-in-time health check (replica sync + slot WAL
 * retention + pg_wal dir size + stream liveness) and resolve with its
 * verdict lines. A raw fetch, NOT fetchJson — this POST mutates nothing
 * in the DB but must never be served from (or written into) the client
 * cache; it's an on-demand probe. Deduped server-side by process-id-tag:
 * a concurrent click resolves immediately with alreadyRunning. */
export async function runHealthCheck(): Promise<HealthCheckResponse> {
  const res = await fetch("/api/monitor/health-check", { method: "POST" });
  if (!res.ok) {
    throw new Error(`health-check failed: HTTP ${res.status}`);
  }
  return (await res.json()) as HealthCheckResponse;
}

// ---------------------------------------------------------------------------
// Start always-on stream services — POST /api/monitor/stream-always
// ---------------------------------------------------------------------------

/** Result of the always-stream starter: `success` = the Task Scheduler
 * task (oxpicious-stream-always) was re-created and triggered — its
 * console session runs the idempotent watchdog starter then
 * scripts/stream_always.sh (the five pollers, sleep 1 apart) and parks,
 * keeping the nohup'd services alive (a transient wsl.exe session's
 * children die with it). Real liveness is the heartbeat table the page
 * already polls: first beats within ~60s. */
export interface StreamAlwaysResponse {
  success: boolean;
  message: string;
}

/** Bring the always-on stream up (watchdog + five pollers + db_health).
 * Resolves as soon as the task is triggered — NOT when services are
 * live. A raw fetch, NOT fetchJson: this POST spawns a scheduled task
 * and must never be served from (or written into) the client cache.
 * Idempotent downstream (watchdog pgrep-guard, pollers max_parallel-
 * guard), so a redundant start is a harmless no-op. */
export async function startStreamAlways(): Promise<StreamAlwaysResponse> {
  const res = await fetch("/api/monitor/stream-always", { method: "POST" });
  if (!res.ok) {
    throw new Error(`stream-always failed: HTTP ${res.status}`);
  }
  return (await res.json()) as StreamAlwaysResponse;
}

// ---------------------------------------------------------------------------
// External resources — monitor.external_resource_stats + audit drill-down
// ---------------------------------------------------------------------------

/** One (service, status_code, date) aggregate of the external request
 * audit, as computed by python -m database.external_resource_stats. */
export interface ExtResourceStatEntry {
  service: string;
  /** Raw HTTP status of the final attempt; null = no response at all
   * (transport error / timeout). */
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

/** The whole trailing window of the per-day aggregates — small by
 * construction (days × services × statuses), pivoted client-side into
 * the trend chart + the per-date breakdown. */
export function fetchExtResourceStats(days = 30): Promise<ExtResourceStatsResponse> {
  return fetchJson<ExtResourceStatsResponse>(
    `/api/monitor/ext-resources/stats?days=${days}`,
  );
}

/** One raw external-request audit row (resource_type / sub_type arrive
 * decoded: http|llm and the host-family / provider label). */
export interface ExtResourceAuditEntry {
  id: string;
  resourceType: string | null;
  subType: string | null;
  url: string;
  statusCode: number | null;
  isFailed: boolean;
  failureReason: string | null;
  durationMs: number | null;
  createdAt: string;
}

export interface ExtResourceAuditResponse {
  success: true;
  service: string;
  date: string;
  entries: ExtResourceAuditEntry[];
}

/** Raw audit rows behind ONE (service, date) bucket. statusCode selects
 * the bucket: a number narrows to that HTTP status, null to the
 * no-response (transport error) bucket, "all" returns every status. */
export function fetchExtResourceAudit(
  service: string,
  date: string,
  statusCode: number | null | "all",
  limit = 100,
): Promise<ExtResourceAuditEntry[]> {
  const status =
    statusCode === "all" ? "" : statusCode === null ? "none" : String(statusCode);
  const params = new URLSearchParams({
    service,
    date,
    limit: String(limit),
  });
  if (status !== "") params.set("status", status);
  return fetchJson<ExtResourceAuditResponse>(
    `/api/monitor/ext-resources/audit?${params.toString()}`,
  ).then((r) => r.entries);
}

/** One per-(service, request_type) pacing / anti-bot config row — the
 *  sleep cooldown, jitter, poll cadence, retries / backoff, timeouts and
 *  toggles a service's request type runs with (resourceType arrives
 *  decoded: http|llm). Source of record is the static JSON config tree
 *  (database/sql/monitoring/static/external_request_config/, one file
 *  per service). */
export interface ExtRequestConfigEntry {
  service: string;
  requestType: string;
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
  /** Per-service config rows (services with their own file in the
   *  static JSON tree). */
  rows: ExtRequestConfigEntry[];
  /** The default.json fallback rows (repo-wide 60s anti-bot cooldown)
   *  for services without their own file — service is the pseudo-name
   *  "default"; use these verbatim for any service missing from rows. */
  defaultRows: ExtRequestConfigEntry[];
}

/** The whole external-request config inventory — one row per request type
 *  per service (a service can issue several request kinds with different
 *  cadences), plus the default fallback rows — fetched whole once by the
 *  External Resources tab. */
export function fetchExtResourceConfigs(): Promise<ExtRequestConfigsResponse> {
  return fetchJson<ExtRequestConfigsResponse>(
    `/api/monitor/ext-resources/configs`,
  );
}

// ---------------------------------------------------------------------------
// Source data audit — monitor.src_data_audit
// ---------------------------------------------------------------------------

/** One per-security source-data coverage row: how many days of raw source
 *  data the security has and its first / last date (written by the
 *  classification + ETF builds; name JOINed in from sec_classification).
 *  board arrives decoded (MAIN | STAR | GEM | BSE, stock rows only). */
export interface SrcDataAuditEntry {
  secType: string;
  code: string;
  name: string | null;
  exchange: string | null;
  board: string | null;
  nDays: number;
  firstDate: string | null;
  lastDate: string | null;
  updatedAt: string;
}

export interface SrcDataAuditResponse {
  success: true;
  generatedAt: string;
  /** Total rows per sec_type, unfiltered — the header's counts. */
  counts: Record<string, number>;
  rows: SrcDataAuditEntry[];
}

/** The source-data coverage audit, filtered server-side: secType narrows
 *  to one security type ("all" = every type), q matches code substring or
 *  display name. Ordered sec_type, code. */
export function fetchSrcDataAudit(
  secType: string,
  q = "",
  limit = 20000,
): Promise<SrcDataAuditResponse> {
  const params = new URLSearchParams({
    sec_type: secType,
    q,
    limit: String(limit),
  });
  return fetchJson<SrcDataAuditResponse>(
    `/api/monitor/src-data-audit?${params.toString()}`,
  );
}
