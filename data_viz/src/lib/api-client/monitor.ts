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

/** Batch pipeline process — LAST-RUN state from the heartbeat table.
 * "never" marks a registered service (service_config tree) with no
 * heartbeat row at all — it has never run (pid / timestamps null). */
export type ProcessState = "running" | "stale" | "stopped" | "never";

export interface ProcessStatus {
  service: string;
  scope: string;
  subScope: string | null;
  /** null only on "never" rows (no process ever). */
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
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
  /** The freq's supposed-run window in ms (null = no checkable period:
   *  always / occasionally / on_demand / once) — a Last beat older than
   *  this renders amber (overdue vs the cadence). */
  freqPeriodMs: number | null;
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
 * batch pipeline processes' last-run state and the run audit trail.
 * A plain fetch, NOT fetchJson: this is the page's LIVE liveness poll
 * (30s cadence) — the shared cache's 10-min TTL would freeze every
 * status chip, pid and beat time after the first load, defeating the
 * auto-refresh the page advertises. */
export async function fetchStreamMonitor(): Promise<MonitorStreamResponse> {
  const res = await fetch("/api/monitor/stream");
  if (!res.ok) {
    throw new Error(`stream monitor failed: HTTP ${res.status}`);
  }
  return (await res.json()) as MonitorStreamResponse;
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

/** One run's log (GET /api/monitor/stream/runs/log): the tail of
 * logs/<date>/<service>.log — the ONE file _common/log_setup wrote for
 * that service that day. `file` is relative to the repo root; found=false
 * carries the file names that were tried (the service file plus the
 * legacy per-component candidates), none of which existed. */
export interface RunLog {
  service: string;
  date: string;
  component: string | null;
  file: string | null;
  candidates: string[];
  found: boolean;
  content: string;
  truncated: boolean;
  totalLines: number;
  sizeBytes: number;
}

/** Fetch ONE run's component log tail. A plain fetch, NOT fetchJson: a
 * live file read — a cached tail would miss lines written after the
 * cache filled. */
export async function fetchRunLog(
  service: string,
  date: string,
  tail = 2000,
): Promise<RunLog> {
  const qs = new URLSearchParams({ service, date, tail: String(tail) });
  const res = await fetch(`/api/monitor/stream/runs/log?${qs.toString()}`);
  if (!res.ok) {
    throw new Error(`run log failed: HTTP ${res.status}`);
  }
  return (await res.json()) as RunLog;
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

/** Result of the always-stream starter: `success` = the hidden keeper
 * session was spawned through the API server's shared WSL runner (same
 * mechanism as every other API-triggered python run — no console
 * window). It runs the idempotent watchdog starter then
 * scripts/stream_always.sh (the five pollers, sleep 1 apart) and parks,
 * keeping the nohup'd services alive (a WSL session's children die with
 * its wsl.exe client, so the client — held by the API server — must stay
 * connected; an API server restart takes the stream down and the button
 * is the recovery). Real liveness is the heartbeat table the page
 * already polls: first beats within ~60s. */
export interface StreamAlwaysResponse {
  success: boolean;
  message: string;
}

/** Bring the always-on stream up (watchdog + five pollers + db_health).
 * Resolves as soon as the keeper session is handed to WSL — NOT when
 * services are live. A raw fetch, NOT fetchJson: this POST spawns a
 * long-lived process and must never be served from (or written into)
 * the client cache. Deduped server-side by process-id-tag while the
 * keeper lives; the downstream starters are idempotent anyway (watchdog
 * pgrep-guard, pollers max_parallel-guard), so a redundant start is a
 * harmless no-op. */
export async function startStreamAlways(): Promise<StreamAlwaysResponse> {
  const res = await fetch("/api/monitor/stream-always", { method: "POST" });
  if (!res.ok) {
    throw new Error(`stream-always failed: HTTP ${res.status}`);
  }
  return (await res.json()) as StreamAlwaysResponse;
}

// ---------------------------------------------------------------------------
// Manual pipeline-process run — POST /api/monitor/stream/run + guard
// ---------------------------------------------------------------------------

/** The freq auto-skip evaluation behind the Run dialog: the service's
 *  service_config freq vs its last successful exit (monitor.service_audit
 *  is_normal_exit). skipRule "within" = skip while last success < periodMs;
 *  "any_success" = skip after any success ever (once); "never" = no
 *  auto-skip (always / occasionally / on_demand). */
export interface ServiceRunGuard {
  service: string;
  freq: string;
  skipRule: "within" | "any_success" | "never";
  periodMs: number | null;
  lastSuccessAt: string | null;
  lastSuccessAgeS: number | null;
  wouldSkip: boolean;
}

/** One cross-service parallel violation: `running` is the live service,
 *  `owner` the config that blocked the pair (the candidate or the running
 *  service), `list` / `entry` the blocking list and its matched entry
 *  ("" = that owner's included whitelist is active and the other side is
 *  not listed). */
export interface ParallelConflict {
  running: string;
  owner: string;
  list: "included_parallel_services" | "excluded_parallel_services";
  entry: string;
}

/** The parallel-services guard preview (the candidate's two lists vs the
 *  live services, both directions). dbError set = the DB was unreachable
 *  and the check was skipped — allowed stays true, flagged. */
export interface ParallelGuard {
  service: string;
  includedParallelServices: string[];
  excludedParallelServices: string[];
  runningChecked: number;
  allowed: boolean;
  dbError: string | null;
  conflicts: ParallelConflict[];
}

export interface ServiceRunGuardResponse extends ServiceRunGuard {
  success: true;
  parallel: ParallelGuard;
}

/** Process-id-tag of a UI-triggered service run — must match the
 * server-side tag format so status polling hits the same registry key. */
export function serviceRunTag(service: string): string {
  return `service-run:${service}`;
}

/** The freq auto-skip + parallel-services evaluation for ONE service —
 *  the Run dialog's preview (would it be skipped or refused right now?).
 *  A plain fetch, NOT fetchJson: a cached verdict would mislead the
 *  confirm dialog. */
export async function fetchServiceRunGuard(
  service: string,
): Promise<ServiceRunGuardResponse> {
  const res = await fetch(
    `/api/monitor/stream/run/guard?service=${encodeURIComponent(service)}`,
  );
  if (!res.ok) {
    throw new Error(`run guard failed: HTTP ${res.status}`);
  }
  const json = (await res.json()) as ServiceRunGuardResponse;
  return json;
}

/** Result of POST /api/monitor/stream/run. started=false + skipped=true =
 * the freq guard auto-skipped (last success inside the freq period);
 * started=false + blocked=true = the parallel-services guard refused
 * (conflict with a live service — force does not bypass it);
 * started=true + alreadyRunning=true = a run of the SAME service is
 * already in flight from this API (dedupe path). */
export interface ServiceRunResponse extends Partial<ServiceRunGuard> {
  success: boolean;
  started: boolean;
  skipped?: boolean;
  blocked?: boolean;
  parallel?: ParallelGuard;
  alreadyRunning?: boolean;
  force?: boolean;
  message: string;
}

/** Trigger one manual run of `python -m <service> [args...]` (fire-and-
 *  forget — the POST resolves at spawn time; heartbeat / service_audit
 *  carry the run from there). A raw fetch, NOT fetchJson: this POST
 *  spawns a process and must never be served from (or written into) the
 *  client cache. */
export async function runService(
  service: string,
  force: boolean,
  args: ReadonlyArray<string> = [],
): Promise<ServiceRunResponse> {
  const res = await fetch("/api/monitor/stream/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service, force, args }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `service run failed: HTTP ${res.status}`);
  }
  return (await res.json()) as ServiceRunResponse;
}

// ---------------------------------------------------------------------------
//  Orchestration tier run — POST /api/monitor/orchestration/run
// ---------------------------------------------------------------------------

/** Result of POST /api/monitor/orchestration/run. alreadyRunning=true =
 *  a run of the SAME tier is already in flight from this API (dedupe
 *  path). */
export interface OrchestrationRunResponse {
  success: boolean;
  started: boolean;
  alreadyRunning?: boolean;
  tier: string;
  message: string;
}

/** Spawn ONE orchestration tier's whole step list — the shared serial
 *  runner `bash scripts/run_steps.sh scripts/orchestration/<file>`
 *  (fire-and-forget; the tier's steps heartbeat onto Pipeline processes
 *  as they start). Raw fetch, same no-cache rule as runService. */
export async function runOrchestrationTier(
  file: string,
): Promise<OrchestrationRunResponse> {
  const res = await fetch("/api/monitor/orchestration/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `orchestration run failed: HTTP ${res.status}`);
  }
  return (await res.json()) as OrchestrationRunResponse;
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

// --- stats refresh (the rerun button) --------------------------------------

/** Process-id-tag of the stats-refresh run — must match the server-side
 *  constant so status polling hits the same registry key. */
export const EXT_RESOURCE_STATS_TAG = "monitor:ext-res-stats";

/** Response of POST /api/monitor/ext-resources/refresh. */
export interface ExtResourceStatsRefreshResponse {
  /** True iff the run was started AND exited with code 0. */
  success: boolean;
  /** True when NO run was started because one is already in flight
   *  (same process-id-tag — dedupe path). */
  alreadyRunning: boolean;
  exitCode: number;
  /** Dates the run re-aggregated (null = unparseable; 0 = already
   *  current, nothing stale). */
  rebuiltDates: number | null;
  /** Tail of the Python stderr (diagnostics). */
  stderrTail: string;
}

/** Trigger one incremental re-run of python -m
 *  database.external_resource_stats and resolve when it exits. Never
 *  throws; failures surface as `{ success: false, stderrTail }`. */
export async function runExtResourceStatsRefresh(): Promise<ExtResourceStatsRefreshResponse> {
  const fail = (stderrTail: string): ExtResourceStatsRefreshResponse => ({
    success: false,
    alreadyRunning: false,
    exitCode: -1,
    rebuiltDates: null,
    stderrTail,
  });
  try {
    const res = await fetch("/api/monitor/ext-resources/refresh", {
      method: "POST",
    });
    if (!res.ok) return fail(`HTTP ${res.status}`);
    return (await res.json()) as ExtResourceStatsRefreshResponse;
  } catch (e) {
    return fail(String(e));
  }
}

/** Running-state of the stats refresh — polled on mount so a page
 *  refresh mid-run restores the button's spinner. Deliberately BYPASSES
 *  the response cache (plain fetch): a live liveness poll. */
export async function fetchExtResourceStatsRefreshRunning(): Promise<boolean> {
  try {
    const res = await fetch(
      `/api/monitor/ext-resources/refresh/status?process_id_tag=${encodeURIComponent(EXT_RESOURCE_STATS_TAG)}`,
    );
    if (!res.ok) return false;
    const json = (await res.json()) as { running?: boolean };
    return json.running ?? false;
  } catch {
    return false;
  }
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
 *  data the security has and its first date (the build-time audit snapshot
 *  written by the classification + ETF builds; name JOINed in from
 *  sec_classification). lastDate is NOT the snapshot — the API re-derives
 *  it per code from the stats source tables at query time, so it moves on
 *  every refresh without waiting for a build. board arrives decoded
 *  (MAIN | STAR | GEM | BSE, stock rows only). */
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
