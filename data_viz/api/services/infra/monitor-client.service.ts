/**
 * Monitor-area client — THE typed HTTP client for the server cluster's
 * ops surface (server_cluster/monitor, FastAPI on 127.0.0.1:8773).
 *
 * Every monitor-route handler proxies to these JSON endpoints instead of
 * spawning local WSL commands (the 2026-11 hard cut): the fleet health
 * check (the former `python -m database.monitoring` spawn), resident
 * aggregation + ensure-up (the former keeper session + pgrep pass), and
 * the orchestration tier runner (the former run_steps.sh sh spawn,
 * retired with the rest of the sh tier).
 */

const MONITOR_BASE = "http://127.0.0.1:8773";

/** One fetch with an abort timeout; Error on ANY failure (network,
 *  non-OK status, timeout) — the routes surface the message. */
async function monitorFetch<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${MONITOR_BASE}${path}`, {
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = (await res.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // non-JSON error body — the status line is the message
      }
      throw new Error(`monitor ${path} → ${detail}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`monitor ${path} timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
//  payloads (mirrors server_cluster/monitor/data_definitions.py)
// ---------------------------------------------------------------------------

export interface MonitorHealthItem {
  label: string;
  ok: boolean;
  message: string;
}

export interface MonitorHealthCheck {
  items: MonitorHealthItem[];
  exit_code: number;
}

export interface MonitorAreaProcess {
  service: string;
  desired?: string | null;
  state?: string | null;
  pid?: number | null;
  detail?: MonitorProcessDetail | Record<string, unknown>;
}

/** The unified detail payload ONE resident row folds (server_cluster/
 *  _common/routers/process_detail.py ProcessDetail — the 2026-11-21
 *  control unification plan Phase 3): supervisor row + heartbeat
 *  evidence (incl. the child-REPORTED progress columns — smallint
 *  label codes; decode at this boundary via label-codes) + the DERIVED
 *  progress (the missing-set oracle from the data tables) + the
 *  service_config control summary + the log tail. */
export interface MonitorProcessDetailHeartbeat {
  service: string;
  pid?: number | null;
  started_at?: string | null;
  beat_age_s?: number | null;
  scope?: string | null;
  sub_scope?: string | null;
  stop_reason?: string | null;
  is_normal_exit?: boolean | null;
  stopped_at?: string | null;
  live_parallel?: number | null;
  max_parallel?: number | null;
  code_fingerprint?: string | null;
  fingerprint_stale?: boolean | null;
  progress_phase?: number | null;
  progress_state?: number | null;
  progress_payload?: Record<string, unknown> | null;
  progress_at?: string | null;
  progress_age_s?: number | null;
}

export interface MonitorProcessDerivedAsset {
  name?: string;
  identity_table?: string;
  intraday_table?: string;
  code_column?: string;
  exchange_column?: string | null;
  exchange?: string | null;
  progress?: {
    trade_date?: string | null;
    n_identity?: number;
    n_total_codes?: number;
    max_time?: string | null;
    n_codes_done?: number;
    close_reached?: boolean;
  };
}

export interface MonitorProcessDetail {
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
  heartbeat?: MonitorProcessDetailHeartbeat | null;
  derived_progress?: {
    kind: string;
    detail: Record<string, unknown>;
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

export interface MonitorAreaFleet {
  service: string;
  area: string;
  base_url: string;
  reachable: boolean;
  error: string | null;
  residents: MonitorAreaProcess[];
}

export interface MonitorAlwaysOnService {
  service: string;
  beat_age_s: number | null;
  pid: number | null;
  stale: boolean;
}

export interface MonitorFleet {
  areas: MonitorAreaFleet[];
  always_on: MonitorAlwaysOnService[];
}

export interface MonitorEnsureUpAction {
  service: string;
  area: string;
  action: string;
  ok: boolean;
  detail: string | null;
}

export interface MonitorEnsureUp {
  actions: MonitorEnsureUpAction[];
  brought_up: number;
}

export interface MonitorOrchestrationStep {
  num: string;
  script: string;
  args: string[];
  src_host: string | null;
  can_be_parallel: boolean;
  note: string | null;
}

export interface MonitorOrchestrationRun {
  tag: string;
  file: string;
  steps: MonitorOrchestrationStep[];
}

export interface MonitorOrchestrationStepStatus {
  num: string;
  script: string;
  /** waiting_priority = held back while a strictly higher-priority run
   *  is in flight (the service_config priority give-way);
   *  skipped_not_due = the arrangement's verdict (the freq window
   *  holds — the arranged run never spawned the step). */
  state:
    | "pending"
    | "waiting_priority"
    | "running"
    | "done"
    | "failed"
    | "skipped_not_due";
  tag: string | null;
  exit_code: number | null;
  priority: number;
}

export interface MonitorOrchestrationStatus {
  tag: string;
  file: string;
  state: "running" | "done" | "failed";
  steps: MonitorOrchestrationStepStatus[];
}

// Non-daily tasks (server_cluster/monitor/non_daily.py — mirrors of the
// data_definitions.py payload models)

export interface MonitorNonDailyTask {
  service: string;
  tier: string;
  step: string;
  script: string;
  args: string[];
  check: string | null;
  rerun_args: string[];
  rerun_eligible: boolean;
  state: "unknown" | "complete" | "missing" | "check_failed";
  state_code: number;
  missing_count: number;
  missing_detail: Record<string, unknown> | null;
  attempts: number;
  last_check_at: string | null;
  last_rerun_at: string | null;
  next_check_at: string | null;
}

export interface MonitorNonDailyInventory {
  tasks: MonitorNonDailyTask[];
}

export interface MonitorNonDailyEvaluate {
  evaluated: number;
  complete: number;
  missing: number;
  check_failed: number;
}

export interface MonitorNonDailyRerun {
  service: string;
  tier: string;
  module: string;
  args: string[];
  dry_run: boolean;
  submitted: boolean;
  exit_code: number | null;
  state_after: string | null;
}

export interface MonitorNonDailyBackfill {
  evaluated: number;
  missing: number;
  due: string[];
  reruns: MonitorNonDailyRerun[];
  detail: string;
}

// Orchestration arrangement (server_cluster/monitor/arrangement.py —
// mirrors of the data_definitions.py payload models)

export interface MonitorArrangementTask {
  service: string;
  tier: string;
  step: string;
  freq: string;
  priority: number;
  state: "unknown" | "due" | "not_due";
  state_code: number;
  /** WHAT the verdict rests on: calendar (the freq rule) or the data
   *  pre-check's reading (server_cluster/monitor/pre_check.py). */
  basis: string;
  basis_code: number;
  missing_count: number;
  missing_detail: Record<string, unknown> | null;
  last_success_at: string | null;
  due_at: string | null;
  arranged_at: string | null;
}

export interface MonitorArrangementInventory {
  tasks: MonitorArrangementTask[];
}

export interface MonitorArrangementRefresh {
  tiers: number;
  services: number;
  due: number;
  not_due: number;
  not_due_services: string[];
  /** Services per basis label (calendar included) — how many verdicts
   *  the data pre-check decided, and what it found. */
  by_basis: Record<string, number>;
  /** Services whose pre-check probe errored — arranged due fail-open. */
  check_failed_services: string[];
}

// ---------------------------------------------------------------------------
//  API
// ---------------------------------------------------------------------------

const TIMEOUT_MS = 10_000;
const RUN_TIMEOUT_MS = 15_000;

/** GET /health-check — the fleet health items (replication + stream
 *  liveness) with the CLI-equivalent 0/1 exit. */
export function monitorHealthCheck(): Promise<MonitorHealthCheck> {
  return monitorFetch<MonitorHealthCheck>(
    "/health-check", { method: "GET" }, TIMEOUT_MS,
  );
}

/** GET /residents — the whole fleet: every area's resident rows plus
 *  the always-on services' heartbeat freshness. */
export function monitorResidents(): Promise<MonitorFleet> {
  return monitorFetch<MonitorFleet>(
    "/residents", { method: "GET" }, TIMEOUT_MS,
  );
}

/** POST /residents/ensure-up — start every desired-running-but-down
 *  resident (and spawn a stale stream watchdog). Idempotent. */
export function monitorEnsureUp(): Promise<MonitorEnsureUp> {
  return monitorFetch<MonitorEnsureUp>(
    "/residents/ensure-up", { method: "POST" }, TIMEOUT_MS,
  );
}

/** POST /orchestration/run — start ONE orchestration tier run (the
 *  monitor area executes the steps serially). */
export function monitorRunOrchestration(
  file: string,
): Promise<MonitorOrchestrationRun> {
  return monitorFetch<MonitorOrchestrationRun>(
    "/orchestration/run",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file }),
    },
    RUN_TIMEOUT_MS,
  );
}

/** GET /orchestration/run/{tag} — one tier run's step statuses. */
export function monitorOrchestrationStatus(
  tag: string,
): Promise<MonitorOrchestrationStatus> {
  return monitorFetch<MonitorOrchestrationStatus>(
    `/orchestration/run/${encodeURIComponent(tag)}`,
    { method: "GET" }, TIMEOUT_MS,
  );
}

/** GET /orchestration — the tier file names the monitor area serves. */
export function monitorOrchestrationFiles(): Promise<string[]> {
  return monitorFetch<string[]>("/orchestration", { method: "GET" },
    TIMEOUT_MS);
}

/** GET /orchestration/{file} — one tier's step plan (the dry-run echo). */
export function monitorOrchestrationPlan(
  file: string,
): Promise<{ file: string; steps: MonitorOrchestrationStep[] }> {
  return monitorFetch(
    `/orchestration/${encodeURIComponent(file)}`, { method: "GET" },
    TIMEOUT_MS,
  );
}

// ---------------------------------------------------------------------------
//  Orchestration arrangement (the freq-window run / don't-run face)
// ---------------------------------------------------------------------------

/** One arrangement pass is a grouped read + bounded upsert batch over
 *  the tier universe — generous but not open-ended. */
const ARRANGEMENT_REFRESH_TIMEOUT_MS = 120_000;

/** POST /arrangement/refresh — one pass over every orchestration tier
 *  file: upsert each service's verdict into
 *  monitor.orchestration_task_state (what the nightly chain's per-tier
 *  arrange side does; the UI's Arrange now action) — the freq-window
 *  rule, or the service's data pre-check where one is declared (one
 *  session-presence query per distinct table slice: well under a second
 *  for one tier, a few seconds — ten cold — over every tier). */
export function monitorArrangementRefresh(): Promise<MonitorArrangementRefresh> {
  return monitorFetch<MonitorArrangementRefresh>(
    "/arrangement/refresh",
    { method: "POST" },
    ARRANGEMENT_REFRESH_TIMEOUT_MS,
  );
}

// The maintainer area (server_cluster/maintainer, 8775) — the cluster's
// ONE chain driver: every nightly-chain pass runs under its lock.
const MAINTAINER_BASE = "http://127.0.0.1:8775";

/** Whether a nightly chain pass is executing right now — the
 *  maintainer's GET /pipeline/last-pass (``running`` = its chain lock).
 *  An unreachable maintainer means no chain can be running (it drives
 *  every one), so that reads false rather than failing the caller. */
export async function monitorPipelineRunning(): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const res = await fetch(`${MAINTAINER_BASE}/pipeline/last-pass`, {
      signal: controller.signal,
    });
    if (!res.ok) return false;
    const body = (await res.json()) as { running?: boolean };
    return body.running === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
//  Non-daily tasks (the completeness checks + weekend backfill face)
// ---------------------------------------------------------------------------

/** A check pass / backfill drive can take a while (one SQL check per
 *  task; the drive re-runs modules) — generous bounds. */
const NON_DAILY_EVALUATE_TIMEOUT_MS = 120_000;
const NON_DAILY_BACKFILL_TIMEOUT_MS = 3_600_000;

/** GET /non-daily — every non-daily tier's tasks with their check
 *  configs and current completeness state. */
export function monitorNonDailyInventory(): Promise<MonitorNonDailyInventory> {
  return monitorFetch<MonitorNonDailyInventory>(
    "/non-daily", { method: "GET" }, TIMEOUT_MS,
  );
}

/** POST /non-daily/evaluate — one check pass; the per-task verdicts
 *  land in monitor.non_daily_task_state. */
export function monitorNonDailyEvaluate(): Promise<MonitorNonDailyEvaluate> {
  return monitorFetch<MonitorNonDailyEvaluate>(
    "/non-daily/evaluate", { method: "POST" }, NON_DAILY_EVALUATE_TIMEOUT_MS,
  );
}

/** POST /non-daily/backfill — the weekend drive: evaluate, re-run each
 *  due missing task, re-check, schedule the weekly retry. */
export function monitorNonDailyBackfill(
  dryRun: boolean,
): Promise<MonitorNonDailyBackfill> {
  return monitorFetch<MonitorNonDailyBackfill>(
    "/non-daily/backfill",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: dryRun }),
    },
    NON_DAILY_BACKFILL_TIMEOUT_MS,
  );
}
