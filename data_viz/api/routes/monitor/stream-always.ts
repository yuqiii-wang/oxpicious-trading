/**
 * POST /health-check + POST /stream-always — the forced DB health check
 * and the always-on stream bring-up; also the boot auto-start helpers
 * that index.ts re-exports for server.ts.
 * Full route docs: see index.ts.
 *
 * 2026-11 hard cut: every handler PROXIES to the server cluster's
 * monitor area (server_cluster/monitor, 127.0.0.1:8773) over JSON —
 * the former local spawns (`python -m database.monitoring`, the
 * stream-always keeper session, the pgrep liveness pass) are gone.
 * The monitor area's own daily 07:00 Asia/Shanghai scheduler is the
 * recurring supervision; this file is the manual + boot recovery path.
 *
 * 2026-11-19 bootstrap exception to the no-spawn cut: when the monitor
 * area itself is unreachable, ensure-up has nothing to proxy to — and
 * the monitor is exactly the service it cannot revive. The stream-always
 * handler then bootstraps the dead fleet from the Windows side (the ONE
 * sanctioned spawn in the API): wake the WSL distro so its /etc/wsl.conf
 * [boot] hook (scripts/wsl_boot_hook.sh → scripts/wsl_boot_services.sh)
 * runs the fleet starter, and — only if the monitor still never answers —
 * run that starter (scripts/stream_watchdog.sh, pgrep-guarded and
 * idempotent) directly. No pipeline is ever spawned here; the revived
 * fleet supervises itself, and the ensure-up proxy is retried once.
 */

import { execFile, exec } from "child_process";
import { Router, type Request, type Response } from "express";
import { wrapAsync } from "../../lib/http.js";
import {
  monitorEnsureUp,
  monitorHealthCheck,
  monitorResidents,
  type MonitorEnsureUp,
} from "../../services/infra/monitor-client.service.js";

const router = Router();

// ---------------------------------------------------------------------------
// monitor-area bootstrap — the one service ensure-up cannot revive
// ---------------------------------------------------------------------------

/** The WSL distro the cluster lives in (the boot hook's registration
 *  target and the conventions' run env). */
const WSL_DISTRO = "Ubuntu-22.04";

/** Cold-boot budget for waking the distro (a dead VM takes seconds to
 *  boot; the [boot] hook itself runs detached). */
const WAKE_TIMEOUT_MS = 15_000;

/** Poll window after the wake: the boot chain is wake → [boot] hook
 *  (mount wait + conda + starter) → watchdog's first pass → monitor
 *  uvicorn binds — tens of seconds end to end. Also covers the "VM and
 *  watchdog alive, monitor dead" shape: the watchdog's next ≤60s pass
 *  revives the monitor on its own. */
const WAKE_POLL_MS = 75_000;

/** Budget for the direct fleet-starter run (pgrep-guarded, exits
 *  immediately after nohup'ing the watchdog). */
const STARTER_TIMEOUT_MS = 15_000;

/** Poll window after the starter: watchdog first pass → monitor up. */
const STARTER_POLL_MS = 75_000;

const MONITOR_POLL_INTERVAL_MS = 5_000;

/** One WSL execFile call with a hard timeout (no shell involved — only
 *  single-token commands go through here). */
function execWsl(args: string[], timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(
      "wsl.exe",
      ["-d", WSL_DISTRO, ...args],
      { timeout: timeoutMs, windowsHide: true },
      (err) => (err ? reject(err) : resolve()),
    );
  });
}

/** Wait (bounded) for the monitor area to answer /health-check — the
 *  same proxy call the route serves, reused as the liveness probe. */
async function waitForMonitorArea(pollMs: number): Promise<boolean> {
  const deadline = Date.now() + pollMs;
  while (Date.now() < deadline) {
    try {
      await monitorHealthCheck();
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, MONITOR_POLL_INTERVAL_MS));
    }
  }
  return false;
}

/** Bring the monitor area back from a dead fleet. Three dead shapes,
 *  one bounded sequence:
 *  1. VM dead (idle shutdown / reboot): the wake call boots the distro —
 *     its [boot] hook starts the watchdog, whose first pass spawns the
 *     monitor.
 *  2. VM alive, watchdog alive, monitor dead: the wake call is a no-op
 *     and the watchdog's own next pass revives the monitor — the first
 *     poll window covers it.
 *  3. VM alive, watchdog ALSO dead (the mutual pair's only
 *     unrecoverable shape): run the pgrep-guarded fleet starter
 *     (scripts/stream_watchdog.sh) once through the conventions' WSL
 *     invocation; the starter's nohup'd watchdog then spawns the monitor
 *     as always.
 *  Returns null when the monitor answers again, else the failure
 *  description for the route's 502 body. */
async function bootstrapMonitorArea(): Promise<string | null> {
  try {
    await execWsl(["--exec", "true"], WAKE_TIMEOUT_MS);
  } catch (err) {
    return `WSL wake failed: ${err instanceof Error ? err.message : String(err)}`;
  }
  if (await waitForMonitorArea(WAKE_POLL_MS)) return null;
  try {
    await new Promise<void>((resolve, reject) => {
      exec(
        `wsl.exe -d ${WSL_DISTRO} -- bash -lc `
          + `"source ~/miniconda3/etc/profile.d/conda.sh && conda activate base `
          + `&& cd /mnt/e/oxpicious-trading && bash scripts/stream_watchdog.sh"`,
        { timeout: STARTER_TIMEOUT_MS, windowsHide: true },
        (err) => (err ? reject(err) : resolve()),
      );
    });
  } catch (err) {
    return `fleet starter failed: ${err instanceof Error ? err.message : String(err)}`;
  }
  if (await waitForMonitorArea(STARTER_POLL_MS)) return null;
  return "monitor area still unreachable after the full bootstrap window";
}

/** POST /api/monitor/health-check — the point-in-time fleet health check
 *  (replica sync + replay backlog + slot WAL retention + pg_wal dir size
 *  vs max_wal_size + always-on heartbeat liveness), served by the
 *  monitor area's GET /health-check (database.monitoring's probes — the
 *  same [ok] / [ALERT] lines the CLI prints, as JSON). */
router.post("/health-check", wrapAsync(async (_req: Request, res: Response) => {
  try {
    const check = await monitorHealthCheck();
    res.json({
      success: true,
      healthy: check.exit_code === 0,
      alreadyRunning: false,
      exitCode: check.exit_code,
      lines: check.items.map((i) => i.message),
      stderrTail: "",
    });
  } catch (err) {
    // The check itself could not run (monitor server down / timed out).
    res.json({
      success: false,
      healthy: false,
      alreadyRunning: false,
      exitCode: -1,
      lines: [],
      stderrTail: err instanceof Error ? err.message : String(err),
    });
  }
}));

/** POST /api/monitor/stream-always — bring the always-on fleet up via
 *  the monitor area's POST /residents/ensure-up: every area supervisor's
 *  desired-running-but-down resident gets an explicit start (bypasses
 *  crash-loop backoff), and a stale stream watchdog is spawned. Idempotent
 *  by contract — over a healthy fleet every row answers already-up.
 *  Recurring supervision stays the stream watchdog's own job (every ~60s)
 *  plus the monitor area's daily 07:00 China-time pass.
 *  When the monitor area itself is unreachable, the handler first
 *  bootstraps the dead fleet (see bootstrapMonitorArea) and retries the
 *  proxy once — the button stays a real recovery path instead of
 *  surfacing HTTP 502 over a dead fleet. */
router.post("/stream-always", wrapAsync(async (_req: Request, res: Response) => {
  let result: MonitorEnsureUp;
  let bootstrapped = false;
  try {
    result = await monitorEnsureUp();
  } catch (err) {
    const bootstrapFail = await bootstrapMonitorArea();
    if (bootstrapFail != null) {
      res.status(502).json({
        success: false,
        started: false,
        message: `monitor area unreachable — bootstrap failed: ${bootstrapFail}`
          + ` (first ensure-up error: ${err instanceof Error ? err.message : String(err)})`,
      });
      return;
    }
    bootstrapped = true;
    try {
      result = await monitorEnsureUp();
    } catch (retryErr) {
      res.status(502).json({
        success: false,
        started: false,
        message: `monitor area came back but ensure-up failed: `
          + (retryErr instanceof Error ? retryErr.message : String(retryErr)),
      });
      return;
    }
  }
  const started = result.actions.filter((a) => a.action === "started");
  const failed = result.actions.filter((a) => !a.ok);
  res.json({
    success: failed.length === 0,
    started: started.length > 0,
    broughtUp: result.brought_up,
    actions: result.actions,
    message: (bootstrapped ? "monitor area was down — bootstrap revived it; " : "")
      + (started.length > 0
        ? `started ${started.map((a) => a.service).join(", ")} — first beats within ~60s`
        : "all resident services already up (ensure-up is idempotent)"),
  });
}));

/** One liveness pass over the always-on fleet — the monitor area's
 *  GET /residents (its fan-out over every area's /processes plus the
 *  always-on heartbeat freshness), replacing the former pgrep pass.
 *  "Running RIGHT NOW" = a live pid on an area supervisor; always-on
 *  entries with no area supervisor (the stream watchdog) are judged by
 *  heartbeat freshness. */
export interface StreamAliveCheck {
  alive: string[];
  dead: string[];
  /** Non-null when the monitor area could not answer — the verdict
   *  lists are then incomplete. */
  error: string | null;
}

export async function checkStreamProcessesAlive(): Promise<StreamAliveCheck> {
  let fleet;
  try {
    fleet = await monitorResidents();
  } catch (err) {
    return {
      alive: [],
      dead: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }
  const alive: string[] = [];
  const dead: string[] = [];
  for (const area of fleet.areas) {
    if (!area.reachable) {
      // An unreachable area server is ONE dead service (its residents
      // are its supervisor's problem, not separately listed here).
      dead.push(area.service);
      continue;
    }
    for (const row of area.residents) {
      if (row.state === "running" && row.pid != null) alive.push(row.service);
      else if (row.desired === "running") dead.push(row.service);
      // desired "stopped" rows are deliberate stops — neither list.
    }
  }
  for (const svc of fleet.always_on) {
    if (svc.stale) dead.push(svc.service);
    else alive.push(svc.service);
  }
  return { alive, dead, error: null };
}

/** API-boot auto-start — when the UI starts, check the always-on fleet
 *  via the monitor area and revive it if anything is down (the same
 *  ensure-up the button fires; idempotent over a healthy fleet). One
 *  pass BOOT_CHECK_DELAY_MS after the server is listening — delayed
 *  because the predecessor's teardown is asynchronous over the first
 *  seconds — and one follow-up pass to confirm the revival (first beats
 *  land within ~60s). Bounded — no recurring supervision; the stream
 *  watchdog + the monitor area's daily 07:00 pass own that. */
const BOOT_CHECK_DELAY_MS = 8_000;
const BOOT_RECHECK_DELAY_MS = 90_000;

export function ensureStreamsAtBoot(): void {
  setTimeout(() => {
    void bootStreamCheck("boot");
  }, BOOT_CHECK_DELAY_MS);
}

async function bootStreamCheck(pass: "boot" | "recheck"): Promise<void> {
  const check = await checkStreamProcessesAlive();
  if (check.error != null) {
    console.error(
      `[monitor/boot] fleet liveness check failed (${check.error})` +
        (pass === "boot" ? " — one recheck in 90s" : ""),
    );
  } else if (check.dead.length === 0) {
    console.log(
      `[monitor/boot] all ${check.alive.length} always-on services are up — nothing to start`,
    );
    return;
  } else {
    console.warn(
      `[monitor/boot] resident services down at UI start: ${check.dead.join(", ")} — ensure-up`,
    );
    try {
      const result = await monitorEnsureUp();
      console.log(
        `[monitor/boot] ensure-up: ${result.brought_up} service(s) brought up`,
      );
    } catch (err) {
      console.error(
        "[monitor/boot] ensure-up failed:",
        err instanceof Error ? err.message : err,
      );
    }
  }
  if (pass === "boot") {
    setTimeout(() => {
      void bootStreamCheck("recheck");
    }, BOOT_RECHECK_DELAY_MS);
  }
}

export default router;
