/**
 * Python script runner service — spawns Python modules via WSL and waits for
 * them to exit.
 *
 * All strategy / build scripts run under WSL (Ubuntu-22.04) with the conda
 * base environment activated. This service centralizes the WSL preamble +
 * spawn logic so strategy services can invoke Python backtests / risk
 * computations without each reimplementing child_process plumbing.
 *
 * Usage:
 *   import { runPythonModule } from "./py-runner.service.js";
 *   const result = await runPythonModule("strategy.ma_spread_trading",
 *     ["--sec-type", "index", "--codes", "000970", "--force"]);
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
//  Runner
// ---------------------------------------------------------------------------

/**
 * Spawn a single Python module via WSL and resolve when it exits.
 *
 * The command run is equivalent to:
 *   wsl -d Ubuntu-22.04 -- bash -lc \
 *     "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base \
 *      && cd /mnt/e/oxpicious-trading && python -m <module> <args...>"
 *
 * Collects stdout/stderr into the returned result. Never throws — spawn
 * errors are captured as `{ code: -1, stderr: <error message> }` so callers
 * can handle all failure modes uniformly via `success === false`.
 *
 * @param module  Python module path (e.g. "strategy.ma_spread_trading")
 * @param args    CLI args to pass after `python -m <module>`
 */
export function runPythonModule(
  module: string,
  args: ReadonlyArray<string>,
): Promise<RunScriptResult> {
  return new Promise((resolve) => {
    const argStr = args.join(" ");
    const cmd = `${WSL_PREAMBLE} && cd ${WSL_PROJECT_ROOT} && python -m ${module} ${argStr}`;
    const child = spawn("wsl", ["-d", "Ubuntu-22.04", "--", "bash", "-lc", cmd], {
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    child.on("close", (code) => {
      const exitCode = code ?? -1;
      resolve({ success: exitCode === 0, stdout, stderr, exitCode });
    });
    child.on("error", (err) => {
      resolve({ success: false, stdout, stderr: stderr + String(err), exitCode: -1 });
    });
  });
}
