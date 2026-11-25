/**
 * API client for the stream monitor page (/api/monitor).
 */
import { fetchJson } from "./_cache";

/** Liveness state of one resident process (the Service Monitor page's
 * first table — the always-on services the stream watchdog supervises;
 * the category used to be called "stream services"). "paused" = the
 * child reports a deliberate pause (2026-11-21 Phase 4 command
 * channel) — a legal state, not a wedge. */
export type ResidentProcessState = "live" | "paused" | "stale" | "stopped" | "down";

export interface ResidentProcessStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  status: ResidentProcessState;
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  restarts24h: number;
  /** monitor.service_heartbeat.live_parallel / the max_parallel cap from
   *  the static JSON config (a registration auto-exits past the cap). */
  liveParallel: number | null;
  maxParallel: number | null;
  /** The child-REPORTED progress at its last safe checkpoint (2026-11-21
   *  control unification Phase 4; index-decoded labels — null = never
   *  reported). The DERIVED per-asset progress rides the fleet fold
   *  (fetchMonitorFleet), not this DB read. */
  progressPhase?: string | null;
  progressState?: string | null;
  progressAt?: string | null;
  progressAgeS?: number | null;
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
  /** The task's start cadence from the service_config/ static tree
   *  (database/sql/monitoring/static/service_config/); "on_demand" for
   *  services without their own file; null for a resident (always
   *  lives — cadence does not apply). */
  freq: string | null;
  /** The freq's supposed-run window in ms (null = no checkable period:
   *  resident / occasionally / on_demand / once) — a Last beat older
   *  than this renders amber (overdue vs the cadence). */
  freqPeriodMs: number | null;
  /** Human-readable timing detail from the same tree entry. */
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
  /** The child-REPORTED progress at its last safe checkpoint (Phase 4;
   *  index-decoded labels — null = never reported). A batch step wedged
   *  in one phase with a stale report is the pre-hung-kill caution. */
  progressPhase?: string | null;
  progressState?: string | null;
  progressAt?: string | null;
  progressAgeS?: number | null;
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
  watchdog: ResidentProcessStatus | null;
  /** The resident processes: always-on pollers, db_health, the
   *  llm_agents daemons — everything the stream watchdog supervises,
   *  minus the watchdog itself. */
  services: ResidentProcessStatus[];
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

// ---------------------------------------------------------------------------
// The fleet fold (2026-11-21 control unification Phase 3) — the ONE
// aggregated query face: UI → Express /api/monitor/fleet → monitor
// /residents → per-area /processes?detail=1 (all server-side). No
// per-process call is ever made from the browser or the API.
// ---------------------------------------------------------------------------

/** The derived (missing-set oracle) progress of ONE poller asset —
 *  computed from the data tables at the owning server. */
export interface FleetAssetProgress {
  trade_date?: string | null;
  n_identity?: number;
  n_total_codes?: number;
  max_time?: string | null;
  n_codes_done?: number;
  close_reached?: boolean;
}

export interface FleetDerivedAsset {
  name?: string;
  identity_table?: string;
  intraday_table?: string;
  code_column?: string;
  exchange_column?: string | null;
  exchange?: string | null;
  progress?: FleetAssetProgress;
}

/** The unified detail payload ONE resident row carries (merged into the
 *  Service Monitor's resident rows by service name). */
export interface FleetProcessDetail {
  service: string;
  area?: string;
  resident?: {
    service: string;
    desired: string;
    state: string;
    pid: number | null;
    adopted?: boolean;
    restarts_recent?: number;
    backoff_until?: number | null;
  } | null;
  heartbeat?: {
    service: string;
    pid?: number | null;
    beat_age_s?: number | null;
    stop_reason?: string | null;
    is_normal_exit?: boolean | null;
    stopped_at?: string | null;
    live_parallel?: number | null;
    fingerprint_stale?: boolean | null;
    progress_phase?: number | null;
    progress_state?: number | null;
    progress_payload?: Record<string, unknown> | null;
    progress_at?: string | null;
    progress_age_s?: number | null;
  } | null;
  derived_progress?: {
    kind: string;
    detail: { assets?: FleetDerivedAsset[] } & Record<string, unknown>;
  } | null;
  config?: {
    resource_class?: string;
    pausable?: boolean;
    max_pause_s?: number;
    max_parallel?: number;
    priority?: number;
    is_resident?: boolean;
  };
  log_tail?: string;
}

export interface MonitorFleetArea {
  service: string;
  area: string;
  base_url: string;
  reachable: boolean;
  error: string | null;
  residents: Array<
    {
      service: string;
      desired?: string | null;
      state?: string | null;
      pid?: number | null;
      /** The unified detail payload (Phase 3 fold) when the area server
       *  answers it; absent on a pre-fold area server. */
      detail?: FleetProcessDetail | Record<string, unknown>;
    } & Record<string, unknown>
  >;
}

export interface MonitorFleetResponse {
  success: true;
  generatedAt: string;
  fleet: {
    areas: MonitorFleetArea[];
    always_on: Array<{
      service: string;
      pid: number | null;
      beat_age_s: number | null;
      stale: boolean;
    }>;
  };
}

/** The fleet fold — the Service Monitor page's SECOND query (beside the
 *  DB-backed /stream): one aggregated call, refreshed on the same 30s
 *  cadence. A plain fetch, NOT fetchJson: the fold must track the fleet
 *  live (the shared cache's 10-min TTL would freeze progress after the
 *  first load). */
export async function fetchMonitorFleet(): Promise<MonitorFleetResponse> {
  const res = await fetch("/api/monitor/fleet");
  if (!res.ok) {
    throw new Error(`fleet fold failed: HTTP ${res.status}`);
  }
  return (await res.json()) as MonitorFleetResponse;
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

export interface DailyRunsResponse {
  success: true;
  /** The service the counts belong to (echoes the query param). */
  service: string;
  /** Shanghai-local day (YYYY-MM-DD) per index — gap-filled, so quiet
   * days are present with a zero count. */
  days: string[];
  /** Pipeline-run counts, index-aligned with `days`. */
  counts: number[];
  /** Non-normal exits per day, index-aligned with `days`. */
  abnormalCounts: number[];
}

export interface DayRunsResponse {
  success: true;
  date: string;
  runs: ServiceAuditEntry[];
}

/** Daily run counts of ONE service over the trailing window (GET
 * /api/monitor/stream/runs/daily) — the per-service run-trend curve the
 * monitor-scope shared ServiceRunTrend mounts (every service-row
 * expansion). A day-grain aggregate over append-only audit history
 * (today's bucket only grows), so the shared cache's 10-min TTL window
 * is fine. */
export function fetchDailyRunCounts(
  service: string,
  days = 90,
): Promise<DailyRunsResponse> {
  return fetchJson<DailyRunsResponse>(
    `/api/monitor/stream/runs/daily?service=${encodeURIComponent(service)}&days=${days}`,
  );
}

/** Every run of ONE Shanghai-local day across ALL services (GET
 * /api/monitor/stream/runs/day) — the day drill-down under the daily
 * run-count curve. Append-only history: shared-cache TTL fine. */
export function fetchDayRuns(
  date: string,
  limit = 2000,
): Promise<ServiceAuditEntry[]> {
  return fetchJson<DayRunsResponse>(
    `/api/monitor/stream/runs/day?date=${encodeURIComponent(date)}&limit=${limit}`,
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
// Start the always-on resident processes — POST /api/monitor/stream-always
// ---------------------------------------------------------------------------

/** Result of the always-stream starter: `success` = the monitor area's
 *  ensure-up pass ran (every desired-running-but-down resident gets an
 *  explicit start; idempotent over a healthy fleet — recurring
 *  supervision stays the watchdog's every ~60s plus the maintainer's
 *  daily 07:00 China-time pass). Real liveness is the heartbeat table
 *  the page already polls: first beats within ~60s. */
export interface StreamAlwaysResponse {
  success: boolean;
  message: string;
}

/** Bring the always-on resident processes up: POST /api/monitor/stream-always
 *  proxies to the monitor area's idempotent ensure-up (every
 *  desired-running-but-down resident gets an explicit start; over a
 *  healthy fleet a no-op). When the monitor area itself is down the API
 *  bootstraps the dead fleet first (wake WSL → boot hook → fleet
 *  starter), so the call can take up to a few minutes before it
 *  resolves; the page's spinner covers the wait. Error responses carry
 *  the route's real failure description — surfaced verbatim, not as a
 *  bare status line. A raw fetch, NOT fetchJson: this POST touches the
 *  resident fleet and must never be served from (or written into) the
 *  client cache. Idempotent end to end (fleet starter pgrep-guard,
 *  services' max_parallel guard, ensure-up no-op over a healthy fleet),
 *  so a redundant click is harmless. */
export async function startStreamAlways(): Promise<StreamAlwaysResponse> {
  const res = await fetch("/api/monitor/stream-always", { method: "POST" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { message?: string };
      if (body?.message) detail = body.message;
    } catch {
      // non-JSON error body — the status line is the message
    }
    throw new Error(`stream-always failed: ${detail}`);
  }
  return (await res.json()) as StreamAlwaysResponse;
}

// ---------------------------------------------------------------------------
// The downward command mailbox — POST /api/monitor/fleet/command
// (2026-11-21 control unification Phase 4)
// ---------------------------------------------------------------------------

/** The downward signal labels (static.label_dim domain service_command:
 *  0 flush_now | 1 pause | 2 resume) — the wire contract both the API
 *  route and the area servers validate. */
export type ServiceCommandLabel = "flush_now" | "pause" | "resume";

/** The enqueue verdict: `message` names when the child acts (it claims
 *  the command within ~5s and holds/releases at its next safe
 *  checkpoint). */
export interface ServiceCommandResponse {
  success: true;
  service: string;
  command: string;
  enqueued: boolean;
  message: string;
}

/** Send ONE downward signal to ONE running child: the API routes it to
 *  the OWNING area server's mailbox enqueue face; the child's heartbeat
 *  thread claims + consumes it atomically (~5s) and acts at its next
 *  safe checkpoint. A 409 carries the class gate's refusal verbatim
 *  (a PAUSE for a not-pausable / gpu-class service) — surfaced as the
 *  thrown Error's message. A raw fetch, NOT fetchJson: a control POST
 *  must never be served from (or written into) the client cache. */
export async function sendServiceCommand(
  service: string,
  command: ServiceCommandLabel,
): Promise<ServiceCommandResponse> {
  const res = await fetch("/api/monitor/fleet/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service, command }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) detail = body.error;
    } catch {
      // non-JSON error body — the status line is the message
    }
    throw new Error(`command ${command} refused for ${service}: ${detail}`);
  }
  return (await res.json()) as ServiceCommandResponse;
}

// ---------------------------------------------------------------------------
// Manual pipeline-process run — POST /api/monitor/stream/run + guard
// ---------------------------------------------------------------------------

/** The freq auto-skip evaluation behind the Run dialog: the service's
 *  service_config freq vs its last successful exit (monitor.service_audit
 *  is_normal_exit). skipRule "within" = skip while last success < periodMs;
 *  "any_success" = skip after any success ever (once); "never" = no
 *  auto-skip (resident (freq null) / occasionally / on_demand). */
export interface ServiceRunGuard {
  service: string;
  freq: string | null;
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

/** Spawn ONE orchestration tier's whole step list — the monitor area's
 *  serial tier runner over scripts/orchestration/<file> (fire-and-forget;
 *  the tier's steps heartbeat onto Pipeline processes as they start; the
 *  maintainer's nightly chain drives the same runner at 16:00 China
 *  time on trading days). Raw fetch, same no-cache rule as runService. */
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

// ---------------------------------------------------------------------------
//  AI usage (the AI tab) — llm.llm_requests aggregates
// ---------------------------------------------------------------------------

/** One (service, model, GMT date) aggregate of the source LLM request
 *  log — the AI tab's usage audit row. */
export interface AiUsageEntry {
  service: string;
  /** The chat model when the request recorded one (null = none / a
   *  web-search POST without a model). */
  model: string | null;
  /** GMT operational day (Beijing 08:00 boundary). */
  date: string;
  requestCount: number;
  failedCount: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  avgDurationMs: number | null;
  maxDurationMs: number | null;
}

export interface AiUsageResponse {
  success: true;
  generatedAt: string;
  /** Distinct sorted dates present in the window (trend x axis). */
  dates: string[];
  /** Distinct sorted services present in the window (trend series). */
  services: string[];
  rows: AiUsageEntry[];
}

/** The trailing window of LLM-request aggregates, one row per
 *  (service, model, date) — small by construction, pivoted client-side
 *  into the trend chart + the per-date breakdown. Plain no-store fetch
 *  (NOT fetchJson): this is LIVE audit state over the request log — the
 *  point of the tab is "what did we ask just now", so a 10-min-TTL cache
 *  entry would serve a stale window. (The deep-agent runs half of the AI
 *  tab reads the run-lifecycle endpoints under /api/ai directly —
 *  fetchSummaryRuns / fetchActiveSummaryRuns in api-client/aiSummary.) */
export async function fetchAiUsage(days = 30): Promise<AiUsageResponse> {
  const res = await fetch(`/api/monitor/ai/usage?days=${days}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`ai usage fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiUsageResponse;
}

// ---------------------------------------------------------------------------
// AI knowledge-graph queries — llm.kg_queries chains (the AI tab)
// ---------------------------------------------------------------------------

/** One logged kg_search CHAIN from llm.kg_queries: the exact query
 * text, its namespace, the chain id (uuid hex, one per successful
 * search), the chain's edge count (each row's depth is its edge's
 * 0-based ordinal, so minDepth is always 0), the entity-description
 * hash the chain is valid under, and when it was logged. */
export interface AiKgQueryEntry {
  query: string;
  /** kg_namespace codes (same vocabulary as llm.kg_entities): 0 DB
   *  semantics, 1 prompts. */
  namespace: number;
  chainId: string;
  edgeCount: number;
  minDepth: number;
  maxDepth: number;
  entityHash: string;
  loggedAt: string;
}

export interface AiKgQueriesResponse {
  success: true;
  generatedAt: string;
  rows: AiKgQueryEntry[];
}

/** The deep agents' kg_search chain cache / log, newest chains first —
 * what the agents asked the knowledge graphs and which chain each ask
 * resolved to. Plain no-store fetch (NOT fetchJson): live audit state
 * over the search log, same freshness rule as fetchAiUsage. */
export async function fetchAiKgQueries(
  limit = 50,
): Promise<AiKgQueriesResponse> {
  const res = await fetch(`/api/monitor/ai/kg-queries?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`ai kg-queries fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiKgQueriesResponse;
}

/** One logged kg_search chain drawn as a subgraph — the chain's edges
 * (depth order, resolved against the LIVE graph, so purged edges drop
 * out) plus the member entities as the node set. Shapes mirror the
 * config client's KgEntity / KgEdge so the Knowledge Graph tab's
 * option builder renders it unchanged. Plain no-store fetch (NOT
 * fetchJson): live runtime state, same freshness rule as
 * fetchAiKgQueries above. */
export interface AiKgChainResponse {
  success: true;
  generatedAt: string;
  namespace: number;
  chainId: string;
  entities: Array<{
    entityId: number;
    name: string;
    entityType: string;
    description: string | null;
    tags: string[];
  }>;
  edges: Array<{
    edgeId: number;
    relation: string;
    subRelation: string | null;
    weight: number | null;
    members: Array<{
      entityId: number;
      name: string | null;
      entityType: string | null;
    }>;
  }>;
}

/** The subgraph of ONE kg_search chain (the AI tab's expandable rows). */
export async function fetchAiKgChain(
  namespace: number,
  chainId: string,
): Promise<AiKgChainResponse> {
  const res = await fetch(
    `/api/monitor/ai/kg-chain?namespace=${namespace}&chain_id=${encodeURIComponent(chainId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`ai kg-chain fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiKgChainResponse;
}

// ---------------------------------------------------------------------------
//  DB workload audit (the DB tab) — monitor.db_table_audit /
//  monitor.db_table_alert / monitor.db_health
// ---------------------------------------------------------------------------

/** Which store the DB tab's workload endpoints read: live = the per-pass
 *  audit table's trailing window (hours) vs archive = the computed daily
 *  rollup table's LIFETIME aggregate per table (no window — the archive
 *  IS the retention). */
export type DbAuditSource = "live" | "archive";

/** One table's LATEST workload audit row: the write side from the
 *  primary (write_rate + the write-heavy flag) and the read side from
 *  the replica (where the API reads). Rows are ACTIVITY-SPARSE — a
 *  table with no row in the window is idle, so writeCheckedAt /
 *  readCheckedAt read as "last active pass". For a partitioned table
 *  the row is the FAMILY aggregate (rates and deltas summed over its
 *  live partitions, flag the max family severity) — the partitions
 *  themselves come from fetchDbPartitions on expand. In archive mode
 *  (source=archive) the row is the table's LIFETIME aggregate over the
 *  daily rollup table instead: ins/upd/del are lifetime day-sum totals,
 *  writeRate/readRate lifetime peaks, nLiveTup/nDeadTup/sizeBytes the
 *  latest archived day's end-of-day snapshots, and the stamps the most
 *  recent active day (windowS always null). */
export interface DbTableEntry {
  schema: string;
  table: string;
  writeCheckedAt: string | null;
  windowS: number | null;
  /** (ins+upd+del) rows/s over the window — the write-heavy metric. */
  writeRate: number | null;
  insRows: number | null;
  updRows: number | null;
  delRows: number | null;
  readCheckedAt: string | null;
  /** scans/s (replica side when the API read it, else the primary's
   *  own read rate; null = no audited read activity). */
  readRate: number | null;
  nLiveTup: number | null;
  nDeadTup: number | null;
  sizeBytes: number | null;
  /** db_write_flag code: 0 ok | 1 amber | 2 red; null = not evaluated. */
  writeFlag: number | null;
  writeFlagLabel: string | null;
  /** Live partition children of this table (0 = not partitioned) —
   *  the expand affordance; null on fetchDbPartitions rows. */
  partitionCount: number | null;
}

/** The workload table's ordering — workload = combined write+read rate
 *  (heaviest first), recent = the later last-active stamp (newest first). */
export type DbTableOrder = "workload" | "recent";

export interface DbTablesResponse {
  success: true;
  generatedAt: string;
  hours: number;
  order: string;
  source: DbAuditSource;
  rows: DbTableEntry[];
}

/** One point of the per-table workload trend — LIVE mode: one active
 *  pass of one table on the primary (idle gaps have no points);
 *  ARCHIVE mode: one GMT operational day of the daily rollup (at = the
 *  day's last active stamp, date = the GMT day itself — browser-local
 *  dates of at can skew a day off the archive's UTC-day grain). */
export interface DbTrendPoint {
  at: string;
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
  source: DbAuditSource;
  /** true = points are the partition-family aggregate of a partitioned
   *  parent (false = a plain table's / a partition's own rows). */
  family: boolean;
  points: DbTrendPoint[];
}

/** One write_flag STATE TRANSITION from the alert trail: raise /
 *  downgrade / clear, with the tripping rate vs the threshold in
 *  effect (severity 0 ok = a clear). */
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

/** The LATEST monitor.db_health row of one Postgres instance (the 60s
 *  sampler — the DB tab's instance vitals panel). */
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

/** The DB tab's per-table workload panel — LATEST audit row per MAIN
 *  table (partition families folded into their parent row) over a
 *  trailing window (activity-sparse), ordered server-side by the given
 *  key (workload | recent) before the limit. live hours=0 = the full
 *  retained history (no lower bound — the sampler's retention prunes
 *  the table), so tables idle in recent windows still show;
 *  source=archive swaps to the daily rollup table's LIFETIME aggregate
 *  per table (hours ignored on the wire, echoed 0). Plain no-store
 *  fetch: live audit state — the point of the tab is "what is hot right
 *  now". */
export async function fetchDbTables(
  hours = 24,
  limit = 500,
  order: DbTableOrder = "workload",
  source: DbAuditSource = "live",
): Promise<DbTablesResponse> {
  const res = await fetch(
    `/api/monitor/db/tables?hours=${hours}&order=${order}&limit=${limit}&source=${source}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`db tables fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbTablesResponse;
}

export interface DbPartitionsResponse {
  success: true;
  generatedAt: string;
  schema: string;
  table: string;
  hours: number;
  source: DbAuditSource;
  rows: DbTableEntry[];
}

/** The live partitions of ONE partitioned parent (the lazy expansion
 *  behind a workload-panel family row) — every partition present, its
 *  audit metrics populated when it was active in the window (live
 *  hours=0 = full retained history; source=archive = the rollup
 *  table's LIFETIME aggregate per partition, hours ignored on the
 *  wire). Plain no-store fetch: live audit state. */
export async function fetchDbPartitions(
  schema: string,
  table: string,
  hours = 24,
  source: DbAuditSource = "live",
): Promise<DbPartitionsResponse> {
  const res = await fetch(
    `/api/monitor/db/partitions?schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}&hours=${hours}&source=${source}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`db partitions fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbPartitionsResponse;
}

/** ONE table's write/read workload trend on the primary (the family
 *  workload trend chart inside an expanded family row of the workload
 *  table — the former page-level drill-down is retired; a partitioned
 *  parent returns its family aggregate — response.family; live hours=0 =
 *  the full retained history; source=archive = the rollup table's
 *  per-GMT-day series over the full archive history, each point stamped
 *  with its operational day — point.date). */
export async function fetchDbTableTrend(
  schema: string,
  table: string,
  hours = 6,
  source: DbAuditSource = "live",
): Promise<DbTrendResponse> {
  const res = await fetch(
    `/api/monitor/db/trend?schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}&hours=${hours}&source=${source}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`db trend fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbTrendResponse;
}

/** The write-heavy alert trail — flag transitions, newest first. */
export async function fetchDbAlerts(limit = 50): Promise<DbAlertsResponse> {
  const res = await fetch(`/api/monitor/db/alerts?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`db alerts fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbAlertsResponse;
}

/** One DISTINCT audited SQL statement: pg_stat_statements' normalized
 *  text (literals -> $n) with its sha256 identity and delta-accumulated
 *  call/time/row totals (cumulative since first seen — no window). */
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

/** The SQL audit rows, ordered by the given key (total_ms | calls |
 *  recent | avg_ms), optionally narrowed to a text substring or hash
 *  prefix. Plain no-store fetch — live audit state. */
export async function fetchDbSql(
  order = "total_ms",
  limit = 100,
  q = "",
): Promise<DbSqlResponse> {
  const res = await fetch(
    `/api/monitor/db/sql?order=${order}&limit=${limit}&q=${encodeURIComponent(q)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`db sql fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbSqlResponse;
}

/** The instance vitals panel: latest db_health row per instance + the
 *  trailing connections / cache-hit / backlog trend (hours=0 = archive:
 *  the full retained db_health history). */
export async function fetchDbInstances(
  hours = 24,
): Promise<DbInstancesResponse> {
  const res = await fetch(`/api/monitor/db/instances?hours=${hours}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`db instances fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as DbInstancesResponse;
}

// ---------------------------------------------------------------------------
// Orchestration arrangement board — what the maintainer arranged to run
// vs skip (the Orchestration page's three sections)
// ---------------------------------------------------------------------------

/** WHAT an arrangement verdict rests on (monitor.orchestration_task_state.
 *  basis — the label arrives already DECODED): calendar = the freq rule
 *  (the service declares no data pre-check); the rest are the nightly
 *  chain's data pre-check over the latest closed sessions. */
export type OrchestrationBasis =
  | "calendar"
  | "data_present"
  | "db_missing"
  | "csv_present"
  | "csv_missing"
  | "check_failed";

/** The data pre-check's session lists behind a verdict (ISO dates). */
export interface OrchestrationPreCheckDetail {
  /** The service's role: fetch (a download), load (a build reading the
   *  downloaded csv), derive (analysis / strategy / a DB-only build). */
  kind?: "fetch" | "load" | "derive";
  /** The data table slices that were probed (a slice = the service's
   *  universe inside a shared table: an exchange, a code family). */
  tables?: string[];
  /** The probed window's first / last expected session. */
  from?: string;
  to?: string;
  /** Expected sessions absent from every probed table slice. */
  missing?: string[];
  /** Absent sessions whose source csv is staged (a final file). */
  csv_present?: string[];
  /** Absent sessions whose source csv is not staged. */
  csv_missing?: string[];
  /** A multi-slice service: the sessions each lacking table slice
   *  misses (which exchange / family is behind). */
  slice_missing?: Record<string, string[]>;
  /** The probe's error (check_failed). */
  error?: string;
}

/** One board task — the shared shape of all three sections' rows (each
 *  expands into the per-service run trend off `service`). */
export interface OrchestrationBoardTask {
  service: string;
  tier: string;
  step: string;
  freq: string;
  priority: number;
  /** The arrangement verdict (monitor.orchestration_task_state — the
   *  Shanghai-calendar freq rule, or the data pre-check for a service
   *  that declares one; the state arrives already DECODED). */
  planState: "unknown" | "due" | "not_due";
  stateCode: number;
  /** The evidence behind the verdict (see OrchestrationBasis). */
  basis: OrchestrationBasis;
  /** Sessions the pre-check found absent (0 under the calendar rule). */
  missingCount: number;
  /** The pre-check's session lists (null under the calendar rule). */
  missingDetail: OrchestrationPreCheckDetail | null;
  lastSuccessAt: string | null;
  /** The calendar boundary the freq rule keys on — for a pre-checked
   *  row a reference only (NULL = no boundary: never succeeded / always
   *  / occasionally / on_demand / once). */
  dueAt: string | null;
  arrangedAt: string | null;
  successSinceArranged: boolean;
  /** Live-heartbeat join — present only in the running section. */
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  /** Non-daily completeness join (nulls when the task has no row) — the
   *  queue's missing / attempts / next-check tooltip. */
  ndState: string | null;
  ndMissingCount: number | null;
  ndAttempts: number | null;
  ndNextCheckAt: string | null;
  /** p95 run duration in seconds — the duration 95% of the service's
   *  last 20 completed normal-exit runs finished within (null = no
   *  completed run on record). */
  p95RunS: number | null;
  /** service_config cadence enrichment. */
  schedule: string | null;
  sourceRef: string | null;
  /** Why the task sits in its section: the queue — the pre-check reading
   *  that made it due (null when the calendar rule decided); no-need —
   *  what holds it (null in the running section). */
  reason: string | null;
}

export interface OrchestrationBoardResponse {
  success: true;
  generatedAt: string;
  running: OrchestrationBoardTask[];
  queue: OrchestrationBoardTask[];
  noNeed: OrchestrationBoardTask[];
}

export interface OrchestrationArrangeResponse extends OrchestrationBoardResponse {
  arranged: {
    tiers: number;
    services: number;
    due: number;
    notDue: number;
    notDueServices: string[];
    /** Services per basis label (calendar included) — how many verdicts
     *  the data pre-check decided, and what it found. */
    byBasis: Record<string, number>;
    /** Services whose pre-check probe errored — arranged due fail-open. */
    checkFailedServices: string[];
  };
}

/** The arrangement board. Plain no-store fetch — the running section is
 *  a live-heartbeat join and the verdicts change with every arrange
 *  pass. */
export async function fetchOrchestrationBoard(): Promise<OrchestrationBoardResponse> {
  const res = await fetch("/api/monitor/orchestration/board", {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`orchestration board fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as OrchestrationBoardResponse;
}

/** One arrangement pass over every tier file NOW (the page's Arrange now
 *  action — the freq rule plus the data pre-check, what the nightly
 *  chain's per-tier arrange side applies), then the refreshed board. */
export async function arrangeOrchestration(): Promise<OrchestrationArrangeResponse> {
  const res = await fetch("/api/monitor/orchestration/arrange", {
    method: "POST",
  });
  if (!res.ok) {
    // The server's own message (e.g. a chain pass is running — the
    // arrange would collide with its per-tier arrange).
    const body = (await res.json().catch(() => null)) as { error?: string } | null;
    throw new Error(
      body?.error ?? `orchestration arrange failed (HTTP ${res.status})`,
    );
  }
  return (await res.json()) as OrchestrationArrangeResponse;
}

// ---------------------------------------------------------------------------
// Non-daily tasks — completeness checks + weekend backfill (the
// Orchestration page's actions)
// ---------------------------------------------------------------------------

export interface NonDailyEvaluateResponse {
  success: true;
  evaluated: number;
  complete: number;
  missing: number;
  checkFailed: number;
}

export interface NonDailyRerun {
  service: string;
  tier: string;
  module: string;
  args: string[];
  dryRun: boolean;
  submitted: boolean;
  exitCode: number | null;
  stateAfter: string | null;
}

export interface NonDailyBackfillResponse {
  success: true;
  evaluated: number;
  missing: number;
  due: string[];
  reruns: NonDailyRerun[];
  detail: string;
}

/** One completeness check pass now (the page's Evaluate action; the
 *  verdicts upsert into monitor.non_daily_task_state). */
export async function evaluateNonDailyTasks(): Promise<NonDailyEvaluateResponse> {
  const res = await fetch("/api/monitor/non-daily/evaluate", {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`non-daily evaluate failed (HTTP ${res.status})`);
  }
  return (await res.json()) as NonDailyEvaluateResponse;
}

/** The weekend backfill drive on demand (dryRun=true lists the due
 *  re-runs without submitting). Can run for minutes when it drives real
 *  re-runs — the caller surfaces a spinner. */
export async function backfillNonDailyTasks(
  dryRun: boolean,
): Promise<NonDailyBackfillResponse> {
  const res = await fetch("/api/monitor/non-daily/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dryRun }),
  });
  if (!res.ok) {
    throw new Error(`non-daily backfill failed (HTTP ${res.status})`);
  }
  return (await res.json()) as NonDailyBackfillResponse;
}

// ---------------------------------------------------------------------------
//  Cautions — the cross-service caution trail (monitor.caution_log):
//  service errors, AI-found suspicions, manual pending bugs; resolvable.
// ---------------------------------------------------------------------------

/** Caution source label (caution_source domain): service | ai | manual. */
export type CautionSource = "service" | "ai" | "manual";
/** Caution severity label (caution_severity domain): info | warning |
 *  error | critical — code order is semantic, best -> worst. */
export type CautionSeverity =
  | "info"
  | "warning"
  | "error"
  | "critical";
export type CautionState = "open" | "resolved" | "all";

/** One caution row: an OPEN row per issue per service (the dedupe
 *  contract — a recurrence bumps occurrenceCount / lastSeenAt;
 *  resolution stamps resolvedTimestamp, a recurrence re-raises fresh). */
export interface CautionEntry {
  id: number;
  service: string;
  source: number;
  sourceLabel: string;
  severity: number;
  severityLabel: string;
  title: string;
  detail: Record<string, unknown>;
  dedupeKey: string;
  occurrenceCount: number;
  isResolved: boolean;
  createdAt: string;
  lastSeenAt: string;
  resolvedTimestamp: string | null;
}

export interface CautionsResponse {
  success: true;
  generatedAt: string;
  state: CautionState;
  rows: CautionEntry[];
}

/** The caution trail, OPEN-first (open before resolved, worst severity
 *  first, most-recent-hit first). Plain no-store fetch (NOT fetchJson):
 *  live working-set state. `date` narrows to one caution day (the GMT
 *  day of first-seen): "today" (default) | "all" | "YYYY-MM-DD". */
export async function fetchCautions(
  state: CautionState = "open",
  source: CautionSource | null = null,
  severity: CautionSeverity | null = null,
  limit = 200,
  date: "today" | "all" | string = "today",
): Promise<CautionsResponse> {
  const qs = new URLSearchParams({ state, limit: String(limit), date });
  if (source != null) qs.set("source", source);
  if (severity != null) qs.set("severity", severity);
  const res = await fetch(`/api/monitor/cautions?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`cautions fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as CautionsResponse;
}

/** Resolve ONE open caution row (POST /cautions/resolve). 404s (an
 *  unknown id / an already-resolved row) throw — the caller refreshes. */
export async function resolveCaution(id: number): Promise<void> {
  const res = await fetch("/api/monitor/cautions/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) {
    throw new Error(`caution resolve failed (HTTP ${res.status})`);
  }
}

/** One caution to register (POST /cautions) — severity required,
 *  source defaults to service, dedupeKey defaults to the title.
 *  logContext (file / keyword / timeFrom / timeTo) rides into
 *  detail.log_context — how to locate the error in the logs; missing
 *  time bounds are stamped with the record time. */
export interface RegisterCautionInput {
  service: string;
  title: string;
  severity: CautionSeverity;
  source?: CautionSource;
  dedupeKey?: string;
  detail?: Record<string, unknown>;
  logContext?: {
    file?: string;
    keyword?: string;
    timeFrom?: string;
    timeTo?: string;
  };
}

/** Register ONE caution row (POST /api/monitor/cautions) — the write
 *  half of the trail (deduped on (service, dedupeKey) over open rows;
 *  a recurrence bumps occurrenceCount server-side). */
export async function registerCaution(input: RegisterCautionInput): Promise<void> {
  const res = await fetch("/api/monitor/cautions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    throw new Error(`caution register failed (HTTP ${res.status})`);
  }
}
