/**
 * Monitor-area client — THE typed HTTP client for the server cluster's
 * ops surface (server_cluster/monitor, FastAPI on 127.0.0.1:8773).
 *
 * Every monitor-route handler proxies to these JSON endpoints instead of
 * spawning local WSL commands (the 2026-11 hard cut): the fleet health
 * check (the former `python -m database.monitoring` spawn), resident
 * aggregation + ensure-up (the former keeper session + pgrep pass), and
 * the orchestration tier runner (the former run_steps.sh spawn).
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
  detail?: Record<string, unknown>;
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
  state: "pending" | "running" | "done" | "failed";
  tag: string | null;
  exit_code: number | null;
}

export interface MonitorOrchestrationStatus {
  tag: string;
  file: string;
  state: "running" | "done" | "failed";
  steps: MonitorOrchestrationStepStatus[];
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
