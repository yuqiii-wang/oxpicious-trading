/**
 * Python runner service — THE seam between the Express routes and the
 * pipeline cmds.
 *
 * Since the server cluster (server_cluster/, resident FastAPI services
 * on 127.0.0.1:8767-8774), NO cmd is spawned here: every module is
 * submitted to its OWNING cluster server (POST /jobs/run) and polled
 * for the result (GET /jobs/result), so the cluster's tag registry —
 * not an in-memory map in THIS process — is the truth about what is
 * running (it survives Express AND cluster restarts). The 2026-11
 * monitor area (8773) absorbed the last legacy spawns (database.*,
 * llm_agents.*, test.*): this process now spawns NOTHING — a module
 * outside every area prefix is a hard error, not a local WSL spawn.
 *
 * The EXTERNAL contract is unchanged for every caller:
 *   import { runPythonModule } from "./py-runner.service.js";
 *   const result = await runPythonModule("strategy.singleton_trading",
 *     ["--sec-type", "index", "--codes", "000970", "--force"]);
 * resolving when the cmd exits with {success, stdout, stderr, exitCode,
 * already_running?}.
 */

// ---------------------------------------------------------------------------
//  Result type
// ---------------------------------------------------------------------------

/** Result of running a Python module (always cluster-submitted). */
export interface RunScriptResult {
  /** True iff the process exited with code 0. */
  success: boolean;
  /** The process's captured stdout (its service-log tail). */
  stdout: string;
  /** The same service-log tail — one log file, both streams. */
  stderr: string;
  /** Exit code of the process (0 on success, -1 on spawn error or an
   *  unknowable exit — a run adopted after a cluster restart). */
  exitCode: number;
  /** True when NO process was spawned because one with the SAME
   *  process-id-tag is still running (dedupe path). */
  already_running?: boolean;
}

// ---------------------------------------------------------------------------
//  Server cluster routing
// ---------------------------------------------------------------------------
// Mirror of server_cluster/_common/config.py DEFAULT_AREAS (+
// cluster_config.json — keep the ports in sync): dotted module prefix →
// the owning area server. The strategist owns BOTH strategy.* and live.*;
// the monitor owns the ops modules (database.*, llm_agents.*, test.*).

const CLUSTER_ROUTES: ReadonlyArray<{ prefix: string; baseUrl: string }> = [
  { prefix: "downloads.", baseUrl: "http://127.0.0.1:8769" },
  { prefix: "builds.", baseUrl: "http://127.0.0.1:8770" },
  { prefix: "analysis.", baseUrl: "http://127.0.0.1:8771" },
  { prefix: "strategy.", baseUrl: "http://127.0.0.1:8772" },
  { prefix: "live.", baseUrl: "http://127.0.0.1:8772" },
  { prefix: "database.", baseUrl: "http://127.0.0.1:8773" },
  { prefix: "llm_agents.", baseUrl: "http://127.0.0.1:8773" },
  { prefix: "test.", baseUrl: "http://127.0.0.1:8773" },
];

/** The owning cluster server's base URL for a module (null = outside
 *  every area — a hard error in runPythonModule). */
function clusterBaseFor(module: string): string | null {
  const hit = CLUSTER_ROUTES.find((r) => module.startsWith(r.prefix));
  return hit ? hit.baseUrl : null;
}

/** Distinct cluster base URLs (the strategist's two prefixes and the
 *  monitor's three share one server each). */
const CLUSTER_BASES = [
  ...new Set(CLUSTER_ROUTES.map((r) => r.baseUrl)),
];

/** The monitor area's base URL (the fleet ops surface: health-check,
 *  residents, ensure-up, orchestration). */
export const MONITOR_BASE_URL = "http://127.0.0.1:8773";

/** One fetch with an abort timeout; null on ANY failure (network error,
 *  non-OK status, timeout). */
async function clusterFetch(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    if (!res.ok) return null;
    return res;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

interface ClusterRunAccepted {
  tag: string;
  module: string;
  pid: number | null;
  already_running: boolean;
  log_path?: string;
}

interface ClusterJobResult {
  tag: string;
  module: string;
  running: boolean;
  exit_code: number | null;
  stdout?: string;
  stderr?: string;
}

const SUBMIT_TIMEOUT_MS = 10_000;
const STATUS_TIMEOUT_MS = 3_000;
const POLL_INTERVAL_MS = 1_500;
/** Consecutive failed result polls before giving up (≈30s of cluster
 *  silence — e.g. the cluster died mid-run). Resolves as a failed run:
 *  the pipeline itself keeps running under its own heartbeat dedupe
 *  either way. */
const MAX_POLL_FAILURES = 20;

/**
 * Submit ONE area cmd to its cluster server and resolve when it exits.
 * Returns null when the cluster cannot be reached/replied (the caller
 * surfaces a failed run — there is no local-spawn fallback).
 */
async function runPythonModuleOnCluster(
  module: string,
  args: ReadonlyArray<string>,
  tag: string,
  baseUrl: string,
): Promise<RunScriptResult | null> {
  let accepted: ClusterRunAccepted;
  try {
    const res = await clusterFetch(
      `${baseUrl}/jobs/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module, args: [...args], tag }),
      },
      SUBMIT_TIMEOUT_MS,
    );
    if (res == null) return null;
    accepted = (await res.json()) as ClusterRunAccepted;
  } catch {
    return null;
  }

  // Dedupe contract verbatim: the same tag in flight → NO new spawn.
  if (accepted.already_running) {
    return {
      success: true,
      already_running: true,
      stdout: "",
      stderr: "",
      exitCode: 0,
    };
  }

  // Poll for the run's outcome — the promise resolves when the cmd
  // exits (what the legacy spawn's `close` event did).
  const url = `${baseUrl}/jobs/result?tag=${encodeURIComponent(tag)}`;
  let failures = 0;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    const res = await clusterFetch(url, { method: "GET" },
      STATUS_TIMEOUT_MS);
    if (res == null) {
      failures += 1;
      if (failures >= MAX_POLL_FAILURES) {
        return {
          success: false,
          stdout: "",
          stderr: `cluster at ${baseUrl} stopped answering while `
            + `polling ${tag} — the run may still be in flight`,
          exitCode: -1,
        };
      }
      continue;
    }
    failures = 0;
    let body: ClusterJobResult;
    try {
      body = (await res.json()) as ClusterJobResult;
    } catch {
      continue;
    }
    if (body.running) continue;
    const exitCode = body.exit_code ?? -1;
    return {
      success: exitCode === 0,
      stdout: body.stdout ?? "",
      stderr: body.stderr ?? "",
      exitCode,
    };
  }
}

// ---------------------------------------------------------------------------
//  Cluster status queries
// ---------------------------------------------------------------------------

/** True iff a process with this process-id-tag is currently running —
 *  ANY cluster server's registry (authoritative across Express
 *  restarts). */
export async function isPythonProcessRunning(tag: string): Promise<boolean> {
  const status = await getPythonProcessStatus([tag]);
  return status[tag] ?? false;
}

/** Running-state for multiple tags at once: { [tag]: boolean } — ORed
 *  across every cluster server's registry. */
export async function getPythonProcessStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, boolean>> {
  const out: Record<string, boolean> = {};
  for (const t of tags) out[t] = false;
  const query = encodeURIComponent(tags.join(","));
  await Promise.all(CLUSTER_BASES.map(async (baseUrl) => {
    const res = await clusterFetch(
      `${baseUrl}/jobs/status?tags=${query}`, { method: "GET" },
      STATUS_TIMEOUT_MS,
    );
    if (res == null) return;
    try {
      const body = (await res.json()) as {
        statuses?: Record<string, { running?: boolean }>;
      };
      for (const [tag, state] of Object.entries(body.statuses ?? {})) {
        if (state?.running === true) out[tag] = true;
      }
    } catch {
      // unreadable body — the tag stays false
    }
  }));
  return out;
}

// ---------------------------------------------------------------------------
//  Runner
// ---------------------------------------------------------------------------

/** Options for runPythonModule. */
export interface RunPythonModuleOptions {
  /**
   * Unique process-id-tag identifying the logical process. When a
   * process with the same tag is already running in the owning
   * cluster's registry, NO new process is spawned and the result
   * resolves immediately with `{ success: true, already_running: true }`
   * — callers surface this to the UI as "process already running" + a
   * spinning button.
   */
  processIdTag?: string;
}

/**
 * Submit a single Python module to its OWNING cluster server and resolve
 * when it exits. This process never spawns anything (2026-11 hard cut —
 * the former legacy WSL spawn and its fallback are gone): a module
 * outside every area prefix resolves as a FAILED run with a clear
 * stderr instead of executing on this computer.
 *
 * @param module  Python module path (e.g. "strategy.singleton_trading")
 * @param args    CLI args to pass after `python -m <module>`
 * @param opts    processIdTag dedupes concurrent runs of the same
 *                logical process (see RunPythonModuleOptions).
 */
export async function runPythonModule(
  module: string,
  args: ReadonlyArray<string>,
  opts: RunPythonModuleOptions = {},
): Promise<RunScriptResult> {
  const baseUrl = clusterBaseFor(module);
  if (baseUrl === null) {
    const msg = `[py-runner] module ${module} is outside every cluster `
      + "area — refusing to run it locally (the owning area must own its "
      + "prefix in server_cluster DEFAULT_AREAS)";
    console.error(msg);
    return { success: false, stdout: "", stderr: msg, exitCode: -1 };
  }
  const tag = opts.processIdTag?.trim() || "";
  const result = await runPythonModuleOnCluster(module, args, tag, baseUrl);
  if (result !== null) return result;
  const msg = `cluster at ${baseUrl} unreachable for ${module} — the run `
    + "was NOT started (no local-spawn fallback; start the cluster "
    + "server and retry)";
  console.error(`[py-runner] ${msg}`);
  return { success: false, stdout: "", stderr: msg, exitCode: -1 };
}
