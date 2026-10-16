/**
 * Python / shell script runner service — spawns Python modules and repo
 * shell scripts via WSL and waits for them to exit.
 *
 * All strategy / build scripts run under WSL (Ubuntu-22.04) with the conda
 * base environment activated. This service centralizes the WSL preamble +
 * spawn logic so strategy services can invoke Python backtests / risk
 * computations without each reimplementing child_process plumbing.
 *
 * Usage:
 *   import { runPythonModule } from "./py-runner.service.js";
 *   const result = await runPythonModule("strategy.singleton_trading",
 *     ["--sec-type", "index", "--codes", "000970", "--force"]);
 *
 * Long-lived keepers (starters whose nohup'd children must SURVIVE) go
 * through the same spawn as any run — a WSL session's children die only
 * when its wsl.exe client exits, and this API server holds the client for
 * the keeper's whole life (runWslScript, e.g. the stream-always keeper in
 * api/routes/monitor.ts POST /stream-always). The flip side: the keeper's
 * lifetime is the API server's lifetime.
 */
import { spawn } from "child_process";

// ---------------------------------------------------------------------------
//  Result type
// ---------------------------------------------------------------------------

/** Result of running a Python module via WSL. */
export interface RunScriptResult {
  /** True iff the process exited with code 0. */
  success: boolean;
  /** Combined stdout from the Python process. */
  stdout: string;
  /** Combined stderr from the Python process. */
  stderr: string;
  /** Exit code of the process (0 on success, -1 on spawn error). */
  exitCode: number;
  /** True when NO process was spawned because one with the SAME
   *  process-id-tag is still running (dedupe path). */
  already_running?: boolean;
}

// ---------------------------------------------------------------------------
//  WSL configuration
// ---------------------------------------------------------------------------

/** WSL project root (matches the /mnt/e mount of the Windows project dir). */
const WSL_PROJECT_ROOT = "/mnt/e/oxpicious-trading";

/** WSL shell prefix that activates conda base + cds into the project. */
const WSL_PREAMBLE =
  "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base";

// ---------------------------------------------------------------------------
//  Process registry — keyed by process-id-tag.
//
//  Every UI-triggered WSL run passes a tag that uniquely identifies the
//  logical process (module + identity params, e.g.
//  "singleton-run:index:000300:macd:10" or "sec-alloc-live:ref"). The
//  registry prevents duplicate spawns of the SAME logical process across
//  races (double click, page refresh, two browser tabs) and lets the UI
//  poll whether a process it did NOT start itself is still running —
//  so a page refresh can put the button straight back into its spinning
//  state until the remote process exits.
// ---------------------------------------------------------------------------
interface RunningProcessEntry {
  module: string;
  tag: string;
  startedAt: number;
}

const runningByTag = new Map<string, RunningProcessEntry>();

/** True iff a process with this process-id-tag is currently running. */
export function isPythonProcessRunning(tag: string): boolean {
  return runningByTag.has(tag);
}

/** Running-state for multiple tags at once: { [tag]: boolean }. */
export function getPythonProcessStatus(
  tags: ReadonlyArray<string>,
): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const t of tags) out[t] = runningByTag.has(t);
  return out;
}

/** Metadata for a running tag (null when not running). */
export function getRunningProcess(tag: string): RunningProcessEntry | null {
  return runningByTag.get(tag) ?? null;
}

// ---------------------------------------------------------------------------
//  Runner
// ---------------------------------------------------------------------------

/** Options for runPythonModule. */
export interface RunPythonModuleOptions {
  /**
   * Unique process-id-tag identifying the logical process. When a process
   * with the same tag is already running, NO new process is spawned and
   * the result resolves immediately with
   * `{ success: true, already_running: true }` — callers surface this to
   * the UI as "process already running" + a spinning button.
   */
  processIdTag?: string;
}

/**
 * Spawn ONE WSL bash -lc command and resolve when it exits — the shared
 * spawn core behind runPythonModule / runWslScript.
 *
 * Collects stdout/stderr into the returned result. Never throws — spawn
 * errors are captured as `{ code: -1, stderr: <error message> }` so callers
 * can handle all failure modes uniformly via `success === false`.
 *
 * @param command Full bash -lc command string (preamble + cd + the work).
 * @param label   What runs, for the process registry (e.g. "python -m x").
 * @param opts    processIdTag dedupes concurrent runs of the same
 *                logical process (see RunPythonModuleOptions).
 */
function runWslCommand(
  command: string,
  label: string,
  opts: RunPythonModuleOptions = {},
): Promise<RunScriptResult> {
  const tag = opts.processIdTag?.trim() || undefined;

  // Dedupe: same logical process already in flight → skip the spawn.
  if (tag && runningByTag.has(tag)) {
    return Promise.resolve({
      success: true,
      already_running: true,
      stdout: "",
      stderr: "",
      exitCode: 0,
    });
  }

  return new Promise((resolve) => {
    const child = spawn("wsl", ["-d", "Ubuntu-22.04", "--", "bash", "-lc", command], {
      windowsHide: true,
    });
    if (tag) runningByTag.set(tag, { module: label, tag, startedAt: Date.now() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    child.on("close", (code) => {
      if (tag) runningByTag.delete(tag);
      const exitCode = code ?? -1;
      resolve({ success: exitCode === 0, stdout, stderr, exitCode });
    });
    child.on("error", (err) => {
      if (tag) runningByTag.delete(tag);
      resolve({ success: false, stdout, stderr: stderr + String(err), exitCode: -1 });
    });
  });
}

/**
 * Spawn a single Python module via WSL and resolve when it exits.
 *
 * The command run is equivalent to:
 *   wsl -d Ubuntu-22.04 -- bash -lc \
 *     "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base \
 *      && cd /mnt/e/oxpicious-trading && python -m <module> <args...>"
 *
 * @param module  Python module path (e.g. "strategy.singleton_trading")
 * @param args    CLI args to pass after `python -m <module>`
 * @param opts    processIdTag dedupes concurrent runs of the same
 *                logical process (see RunPythonModuleOptions).
 */
export function runPythonModule(
  module: string,
  args: ReadonlyArray<string>,
  opts: RunPythonModuleOptions = {},
): Promise<RunScriptResult> {
  const argStr = args.join(" ");
  const cmd = `${WSL_PREAMBLE} && cd ${WSL_PROJECT_ROOT} && python -m ${module} ${argStr}`;
  return runWslCommand(cmd, `python -m ${module}`, opts);
}

/**
 * Spawn ONE repo shell command via WSL and resolve when it exits — the
 * generic form of runPythonModule (same conda preamble + cd, any command).
 *
 * Also the home of long-lived keepers: the spawned wsl.exe stays
 * connected for as long as the command runs (windowsHide — no console
 * window), so a session that parks on `sleep infinity` holds its nohup'd
 * children alive for the lifetime of THIS API server.
 *
 * @param command Full bash command after the preamble (the cd is appended).
 * @param label   What runs, for the process registry.
 * @param opts    processIdTag dedupes concurrent runs of the same logical
 *                process (see RunPythonModuleOptions).
 */
export function runWslScript(
  command: string,
  label: string,
  opts: RunPythonModuleOptions = {},
): Promise<RunScriptResult> {
  const cmd = `${WSL_PREAMBLE} && cd ${WSL_PROJECT_ROOT} && ${command}`;
  return runWslCommand(cmd, label, opts);
}
