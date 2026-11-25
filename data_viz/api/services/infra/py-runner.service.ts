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
 *
 * 2026-11-18 async submit: long jobs (strategy run/train) must not hold
 * the HTTP request open until the process exits, so submitPythonModule
 * resolves at the cluster ACCEPT (no poll loop) and the outcome travels
 * through the status queries instead — getPythonProcessDetails (the
 * cluster /jobs/status payload: running/pid/started_at/exit_code per
 * tag) and getPythonProcessResult (the /jobs/result outcome + log tail).
 * The registry is the truth either way, so both transports observe the
 * same process.
 *
 * 2026-11-16 recovery: an unreachable owning area no longer fails the
 * run outright — ONE monitor ensure-up pass revives the area server (the
 * seven servers are the monitor's supervised residents) and the submit
 * is retried once. Still no local spawn: the run is cluster-submitted
 * or not started. The same pass fixed the masquerade: a definitive
 * cluster rejection (the strategy UI's comma-joined selection made tags
 * fail the cluster's charset → 422) surfaced as "cluster unreachable" —
 * tags are now sanitized to the cluster charset and rejections are
 * reported verbatim instead of triggering the recovery path.
 */

import { monitorEnsureUp } from "./monitor-client.service.js";
// Runs that never STARTED (no-area refusal, unreachable area) leave no
// heartbeat / service_audit trail — they land in the caution trail
// instead (monitor.caution_log, the Cautions tab).
import { recordCaution } from "../caution-log.service.js";

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

/** Result of SUBMITTING a Python module (submitPythonModule) — the
 *  receipt of a fire-and-poll job. The process's eventual outcome is
 *  NOT in this payload: query getPythonProcessDetails /
 *  getPythonProcessResult with the process-id-tag. */
export interface SubmitScriptResult {
  /** True when the cluster accepted the job (it may still fail later —
   *  poll the status endpoints for the outcome). */
  accepted: boolean;
  /** True when NO process was spawned because one with the SAME
   *  process-id-tag is still running (dedupe path). */
  already_running: boolean;
  /** The spawned child's pid (null while unknown / deduped). */
  pid: number | null;
  /** The sanitized tag the run is registered under. */
  processIdTag: string;
  /** The verbatim failure reason when accepted is false (cluster
   *  rejection text or the unreachable-area operator message). */
  error?: string;
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

// ---------------------------------------------------------------------------
//  Unreachable-area recovery (2026-11-16)
// ---------------------------------------------------------------------------

/** How long to wait for a revived area server to answer /health after a
 *  monitor ensure-up pass (a FastAPI boot is fast; the bound covers a
 *  slow machine). */
const AREA_REVIVE_WAIT_MS = 60_000;
const AREA_REVIVE_POLL_MS = 2_000;

/** One recovery pass for a dead area server: the monitor area's
 *  POST /residents/ensure-up (idempotent — the seven cluster servers are
 *  its supervised residents, so a down strategist/builder/… gets an
 *  explicit start), then a bounded /health wait for the boot. True when
 *  the area answers again. False when the MONITOR itself is unreachable
 *  (nothing can start anything) or the area never came back. */
async function ensureAreaUp(baseUrl: string): Promise<boolean> {
  try {
    const result = await monitorEnsureUp();
    console.warn(
      `[py-runner] ensure-up: ${result.brought_up} resident(s) brought `
        + "up while recovering a cluster area",
    );
  } catch (err) {
    console.error(
      "[py-runner] ensure-up unreachable — cannot recover the cluster:",
      err instanceof Error ? err.message : err,
    );
    return false;
  }
  const deadline = Date.now() + AREA_REVIVE_WAIT_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, AREA_REVIVE_POLL_MS));
    const res = await clusterFetch(
      `${baseUrl}/health`, { method: "GET" }, STATUS_TIMEOUT_MS,
    );
    if (res !== null) return true;
  }
  return false;
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

/** The cluster's opaque-but-safe process-id-tag charset (mirror of
 *  server_cluster/_common/routers/jobs.py TAG_RE ^[\w:@.\-+=]{1,200}$ —
 *  a tag outside it is a definitive 422, e.g. the strategy UI's
 *  comma-joined selection segment). */
const TAG_SAFE_RE = /[^\w:@.+\-=]/g;

/** Make a caller tag fit the cluster's tag charset: every unsafe char
 *  (typically the "," that joins a multi-family selection) becomes "+",
 *  deterministically — UI-passed tags and route-default tags built from
 *  the same raw identity converge on the same sanitized string. */
function clusterSafeTag(tag: string): string {
  return tag.replace(TAG_SAFE_RE, "+").slice(0, 200);
}

/** ONE submit POST — distinguishes a definitive cluster rejection
 *  (non-OK status; the body's error text is surfaced verbatim) from a
 *  transport failure (null — the caller's ensure-up recovery path). */
async function clusterSubmit(
  baseUrl: string,
  body: unknown,
  timeoutMs: number,
): Promise<{ accepted: ClusterRunAccepted } | { rejected: string } | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl}/jobs/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = (await res.json()) as { detail?: string; error?: string };
        if (j?.detail) detail = j.detail;
        else if (j?.error) detail = j.error;
      } catch {
        // non-JSON error body — the status line is the message
      }
      return { rejected: detail };
    }
    return { accepted: (await res.json()) as ClusterRunAccepted };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
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
 * ONE submit POST with the 2026-11-16 recovery pass: on a TRANSPORT
 * failure (a definitive rejection is returned as-is for the caller to
 * surface verbatim), one monitor ensure-up revives the owning area,
 * then one submit retry. Returns null when the cluster still cannot be
 * reached — there is no local-spawn fallback.
 */
async function submitWithRecovery(
  module: string,
  args: ReadonlyArray<string>,
  tag: string,
  baseUrl: string,
): Promise<{ accepted: ClusterRunAccepted } | { rejected: string } | null> {
  let submitted = await clusterSubmit(
    baseUrl, { module, args: [...args], tag }, SUBMIT_TIMEOUT_MS,
  );
  if (submitted !== null) return submitted;
  console.warn(
    `[py-runner] cluster at ${baseUrl} unreachable for ${module} — `
      + "monitor ensure-up, then one submit retry",
  );
  if (!(await ensureAreaUp(baseUrl))) return null;
  const retry = await clusterSubmit(
    baseUrl, { module, args: [...args], tag }, SUBMIT_TIMEOUT_MS,
  );
  if (retry !== null) {
    console.log(
      `[py-runner] ${module} submitted after ensure-up revived ${baseUrl}`,
    );
  }
  return retry;
}

/**
 * Poll ONE accepted job's outcome until the cmd exits (what the legacy
 * spawn's `close` event did). Only submit transport failures reach the
 * ensure-up recovery path — poll silence resolves as a FAILED run with
 * the may-still-be-in-flight caveat.
 */
async function pollClusterJobResult(
  baseUrl: string,
  tag: string,
): Promise<RunScriptResult> {
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
 *  across every cluster server's registry. Tags are sanitized to the
 *  cluster charset first (the registry keys are the sanitized tags
 *  registered at submit; an unsanitized caller tag would never match). */
export async function getPythonProcessStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, boolean>> {
  const out: Record<string, boolean> = {};
  for (const t of tags) out[clusterSafeTag(t)] = false;
  const query = encodeURIComponent(Object.keys(out).join(","));
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

/** One tag's enriched registry entry — the snake_case passthrough of
 *  the cluster RunRegistry.status_many payload (GET /jobs/status).
 *  started_at/ended_at are epoch seconds; exit_code null = unfinished
 *  or unknowable (a run adopted after a cluster restart). */
export interface PythonProcessDetails {
  running: boolean;
  known: boolean;
  pid: number | null;
  started_at: number | null;
  ended_at: number | null;
  exit_code: number | null;
  adopted: boolean;
}

/** Enriched running-state for multiple tags at once — same routing and
 *  sanitization as getPythonProcessStatus, but the FULL registry entry
 *  per tag (running/pid/started_at/ended_at/exit_code/adopted) instead
 *  of a bare boolean. Unknown tags answer {running: false, known:
 *  false}; a cluster that cannot be reached leaves the tag unknown. */
export async function getPythonProcessDetails(
  tags: ReadonlyArray<string>,
): Promise<Record<string, PythonProcessDetails>> {
  const out: Record<string, PythonProcessDetails> = {};
  for (const t of tags) {
    out[clusterSafeTag(t)] = {
      running: false, known: false, pid: null,
      started_at: null, ended_at: null, exit_code: null, adopted: false,
    };
  }
  const query = encodeURIComponent(Object.keys(out).join(","));
  await Promise.all(CLUSTER_BASES.map(async (baseUrl) => {
    const res = await clusterFetch(
      `${baseUrl}/jobs/status?tags=${query}`, { method: "GET" },
      STATUS_TIMEOUT_MS,
    );
    if (res == null) return;
    try {
      const body = (await res.json()) as {
        statuses?: Record<string, Partial<PythonProcessDetails>>;
      };
      for (const [tag, state] of Object.entries(body.statuses ?? {})) {
        if (!(tag in out) || state?.known !== true) continue;
        out[tag] = {
          running: state.running === true,
          known: true,
          pid: state.pid ?? null,
          started_at: state.started_at ?? null,
          ended_at: state.ended_at ?? null,
          exit_code: state.exit_code ?? null,
          adopted: state.adopted === true,
        };
      }
    } catch {
      // unreadable body — the tag stays unknown
    }
  }));
  return out;
}

/** One run's outcome from the owning cluster server's GET /jobs/result:
 *  status fields + the service-log tail as both stdout/stderr (one log
 *  file, both streams). known=false for an unknown or LRU-evicted tag
 *  (or a cluster that cannot be reached). */
export interface PythonProcessResult {
  known: boolean;
  running: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
}

export async function getPythonProcessResult(
  tag: string,
): Promise<PythonProcessResult> {
  const safe = clusterSafeTag(tag);
  const unknown: PythonProcessResult = {
    known: false, running: false, exit_code: null, stdout: "", stderr: "",
  };
  const answers = await Promise.all(CLUSTER_BASES.map(async (baseUrl) => {
    const res = await clusterFetch(
      `${baseUrl}/jobs/result?tag=${encodeURIComponent(safe)}`,
      { method: "GET" }, STATUS_TIMEOUT_MS,
    );
    if (res == null) return null; // 404 (unknown/evicted) or transport failure
    try {
      const body = (await res.json()) as Partial<PythonProcessResult>;
      if (typeof body.running !== "boolean") return null;
      const answer: PythonProcessResult = {
        known: true,
        running: body.running,
        exit_code: body.exit_code ?? null,
        stdout: body.stdout ?? "",
        stderr: body.stderr ?? "",
      };
      return answer;
    } catch {
      return null;
    }
  }));
  return answers.find((a) => a !== null) ?? unknown;
}

// ---------------------------------------------------------------------------
//  The downward command mailbox (2026-11-21 control unification Phase 4)
// ---------------------------------------------------------------------------

/** The verdict of ONE command enqueue at the owning area server:
 *  `enqueued` true = the mailbox row is in; false = refused, `refused`
 *  carries the gate's reason verbatim (the route 409s with it). */
export interface ServiceCommandVerdict {
  enqueued: boolean;
  refused?: string;
}

/** Send ONE cooperative command (flush_now | pause | resume) down to a
 *  running child: POST the OWNING area server's
 *  ``/processes/{service}/command`` (the Phase 4 mailbox's enqueue face
 *  — the child's heartbeat thread claims + consumes it atomically at
 *  its next safe checkpoint; SIGTERM stays the backstop). The area
 *  server gates the command class (a PAUSE for a not-pausable /
 *  gpu-class service is a 409 with the reason — surfaced verbatim as a
 *  refusal). Transport failure throws (the route 502s). */
export async function sendServiceCommand(
  service: string,
  command: string,
  payload?: Record<string, unknown>,
): Promise<ServiceCommandVerdict> {
  const baseUrl = clusterBaseFor(service);
  if (baseUrl == null) {
    return {
      enqueued: false,
      refused: `${service} matches no cluster area's module prefixes — `
        + "nothing owns it, so no command can be delivered",
    };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SUBMIT_TIMEOUT_MS);
  try {
    const res = await fetch(
      `${baseUrl}/processes/${encodeURIComponent(service)}/command`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, payload: payload ?? null }),
        signal: controller.signal,
      },
    );
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = (await res.json()) as { detail?: string };
        if (j?.detail) detail = j.detail;
      } catch {
        // non-JSON error body — the status line is the message
      }
      return { enqueued: false, refused: detail };
    }
    return { enqueued: true };
  } catch (err) {
    throw new Error(
      `owning server at ${baseUrl} unreachable for ${service}: `
        + (err instanceof Error ? err.message : String(err)),
    );
  } finally {
    clearTimeout(timer);
  }
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

/** The no-area hard refusal (2026-11): console + a caution row (the
 *  module never started, so no heartbeat will ever record it), and the
 *  operator-facing message text. */
function refuseOutsideArea(
  module: string,
  args: ReadonlyArray<string>,
): string {
  const msg = `[py-runner] module ${module} is outside every cluster `
    + "area — refusing to run it locally (the owning area must own its "
    + "prefix in server_cluster DEFAULT_AREAS)";
  console.error(msg);
  void recordCaution({
    service: "data_viz.api",
    source: "service",
    severity: "error",
    title: `py-runner: ${module} outside every cluster area — run refused`,
    dedupeKey: `py-runner:no-area:${module}`,
    detail: { module, args: [...args], error: msg },
  });
  return msg;
}

/**
 * Resolve + submit ONE module to its owning cluster area (with the
 * ensure-up recovery pass). Shared by runPythonModule (then awaits the
 * outcome) and submitPythonModule (resolves at the accept). An
 * `{error}` return means no process was started or the submit was
 * definitively refused — the string is the operator-facing reason.
 */
async function submitToCluster(
  module: string,
  args: ReadonlyArray<string>,
  opts: RunPythonModuleOptions,
): Promise<
  | { error: string }
  | { baseUrl: string; tag: string; accepted: ClusterRunAccepted }
> {
  const baseUrl = clusterBaseFor(module);
  if (baseUrl === null) return { error: refuseOutsideArea(module, args) };
  // Charset-safe BEFORE the wire — the cluster 422s unsafe tags (e.g.
  // the strategy UI's comma-joined selection segment), and a rejected
  // submit would masquerade as a dead area server.
  const tag = clusterSafeTag(opts.processIdTag?.trim() || "");
  const submitted = await submitWithRecovery(module, args, tag, baseUrl);
  if (submitted === null) {
    const msg = `cluster at ${baseUrl} unreachable for ${module} — the `
      + "run was NOT started (no local-spawn fallback; an ensure-up "
      + "recovery pass did not revive the area server — start it and "
      + "retry)";
    console.error(`[py-runner] ${msg}`);
    // The run never started (no heartbeat, no service_audit row would
    // exist) — surface it as a caution (the Cautions tab's working set).
    void recordCaution({
      service: "data_viz.api",
      source: "service",
      severity: "error",
      title: `py-runner: cluster unreachable for ${module} — run NOT started`,
      dedupeKey: `py-runner:unreachable:${module}`,
      detail: { module, args: [...args], baseUrl, error: msg },
    });
    return { error: msg };
  }
  if ("rejected" in submitted) {
    return {
      error: `cluster at ${baseUrl} rejected the ${module} submit: `
        + submitted.rejected,
    };
  }
  return { baseUrl, tag, accepted: submitted.accepted };
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
  const submitted = await submitToCluster(module, args, opts);
  if ("error" in submitted) {
    return { success: false, stdout: "", stderr: submitted.error, exitCode: -1 };
  }
  const { accepted } = submitted;
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
  return pollClusterJobResult(submitted.baseUrl, submitted.tag);
}

/**
 * Submit a single Python module to its OWNING cluster server and return
 * at the cluster ACCEPT — the fire-and-poll counterpart of
 * runPythonModule for long jobs (strategy run/train): the HTTP caller
 * gets the receipt ({accepted, already_running, pid, processIdTag})
 * immediately instead of blocking for the process's whole lifetime.
 * The cluster's tag registry stays the truth about the process; its
 * outcome travels through getPythonProcessDetails /
 * getPythonProcessResult under the same tag.
 */
export async function submitPythonModule(
  module: string,
  args: ReadonlyArray<string>,
  opts: RunPythonModuleOptions = {},
): Promise<SubmitScriptResult> {
  const submitted = await submitToCluster(module, args, opts);
  const processIdTag = clusterSafeTag(opts.processIdTag?.trim() || "");
  if ("error" in submitted) {
    return {
      accepted: false,
      already_running: false,
      pid: null,
      processIdTag,
      error: submitted.error,
    };
  }
  const { accepted } = submitted;
  console.log(
    `[py-runner] ${module} submitted as ${accepted.tag}`
      + (accepted.already_running ? " (deduped — already running)" : ""),
  );
  return {
    accepted: true,
    already_running: accepted.already_running,
    pid: accepted.pid,
    processIdTag: accepted.tag,
  };
}
