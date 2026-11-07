/**
 * POST /health-check + POST /stream-always — the forced DB health check
 * and the always-on stream keeper start; also the boot auto-start helpers
 * that index.ts re-exports for server.ts.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  isPythonProcessRunning,
  runPythonModule,
  runWslScript,
} from "../../services/py-runner.service.js";
import { SERVICE_CATALOG } from "./_shared.js";

const router = Router();

/** POST /api/monitor/health-check — force-trigger the point-in-time
 *  health check (`python -m database.monitoring`: replica sync + replay
 *  backlog + slot WAL retention + pg_wal dir size vs max_wal_size +
 *  downloads-stream heartbeat liveness) and return its [ok] / [ALERT]
 *  lines. The run also refreshes the check's own heartbeat row, so the
 *  page's stream table picks the forced run up on its next poll. Deduped
 *  by process-id-tag across double clicks / tabs (shared py-runner). */
router.post("/health-check", async (_req: Request, res: Response) => {
  const result = await runPythonModule("database.monitoring", [], {
    processIdTag: "monitor:health-check",
  });
  const lines = result.stdout
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith("[ok]") || l.startsWith("[ALERT]"));
  res.json({
    // success = the check process itself ran (exit code is the health
    // verdict, so a spawn failure is the only non-success here).
    success: result.exitCode !== -1,
    healthy: result.success,
    alreadyRunning: result.already_running === true,
    exitCode: result.exitCode,
    lines,
    stderrTail: result.stderr.slice(-1000),
  });
});

/** POST /api/monitor/stream-always — bring the always-on stream up: the
 *  idempotent supervisor starter (scripts/stream_watchdog.sh) followed by
 *  the staggered poller starter (scripts/stream_always.sh — nohup-spawns
 *  the five downloads.stream.* pollers, sleep 1 apart), then park. Both
 *  starters are idempotent (pgrep guards + max_parallel self-exit), so
 *  starting over a healthy stream is a no-op. Restarts from here on are
 *  the watchdog's own job (database/stream_watchdog.py — heartbeat-based,
 *  every ~60s: a poller or the db_health sampler that died or auto-exited
 *  at startup is re-attempted there, its live_parallel healed from pid
 *  liveness), not this session's.
 *
 *  The session runs through the shared py-runner spawn — a hidden wsl.exe
 *  child of THIS API server (windowsHide, no cmd window; the mechanism
 *  every other API-triggered python run uses). A WSL session's nohup'd
 *  children die with its wsl.exe client, so the client must stay
 *  connected: the API server holds it for the session's whole life, which
 *  also means an API server exit takes the stream down — the boot check
 *  (ensureStreamsAtBoot) revives it at the next API start, the button is
 *  the manual recovery (deduped by process-id-tag while the session
 *  lives). The services' real liveness is the heartbeat table (first
 *  beats within ~60s). Session output lands in
 *  logs/<date>/ui_stream_always.log. */
const STREAM_ALWAYS_TAG = "monitor:stream-always";

/** The session command: date's log dir, append everything to the starter
 *  log, idempotent starters, then park (pgrep name stream-always-keeper
 *  kills just the parked session, not the nohup'd services). */
const STREAM_ALWAYS_KEEPER_CMD = [
  "mkdir -p logs/$(date +%F)",
  "exec >> logs/$(date +%F)/ui_stream_always.log 2>&1",
  "bash scripts/stream_watchdog.sh",
  "bash scripts/stream_always.sh",
  "echo [ui] stream-always keeper session up $(date +%T)",
  "exec -a stream-always-keeper sleep infinity",
].join(" && ");

/** Spawn the stream-always keeper session fire-and-forget — the body of
 *  POST /stream-always, shared with the API-boot auto-start
 *  (ensureStreamsAtBoot). started=false when the tag deduped: a keeper
 *  spawned by THIS API server is already up. */
export function startStreamAlwaysKeeper(): {
  started: boolean;
  alreadyRunning: boolean;
} {
  if (isPythonProcessRunning(STREAM_ALWAYS_TAG)) {
    return { started: false, alreadyRunning: true };
  }
  // Fire-and-forget: the caller resolves as soon as the session is handed
  // to WSL. The promise resolves only when the keeper session DIES (API
  // server exit kills the client, WSL tears the session + services down)
  // — log it loudly so a dead stream is explainable from the API log.
  void runWslScript(STREAM_ALWAYS_KEEPER_CMD, "stream-always keeper", {
    processIdTag: STREAM_ALWAYS_TAG,
  }).then(() => {
    console.error(
      "[monitor/stream-always] keeper session exited — the always-on stream died with it; the next API start (or the button) revives it",
    );
  });
  return { started: true, alreadyRunning: false };
}

router.post("/stream-always", async (_req: Request, res: Response) => {
  const start = startStreamAlwaysKeeper();
  if (start.alreadyRunning) {
    res.json({
      success: true,
      started: false,
      alreadyRunning: true,
      message:
        "stream-always keeper session already up (spawned by this API server) — deduped, no new session",
    });
    return;
  }
  res.json({
    success: true,
    started: true,
    message:
      "keeper session spawned (hidden) — watchdog starter then the five pollers staggered, first beats within ~60s",
  });
});

/** One pgrep pass over the always-on catalog: which services currently
 *  have a live python process in WSL. Process liveness — not the
 *  heartbeat table — is what the boot check needs: beats lag ~60s behind
 *  a spawn and stay fresh ~STALE_AFTER_S after a kill, so heartbeat rows
 *  cannot answer "running RIGHT NOW" at UI start (an API restart kills
 *  the whole stream yet leaves fresh-looking rows behind). The pattern
 *  mirrors stream_watchdog.sh's own guard; the checker shell's cmdline
 *  contains the module names but never the " -m <module>" sequence, so
 *  it cannot self-match (validated: a bogus module reports dead). */
export interface StreamAliveCheck {
  alive: string[];
  dead: string[];
  /** Non-null when the pgrep pass could not run or its output was
   *  unreadable — the verdict lists are then incomplete. */
  error: string | null;
}

export async function checkStreamProcessesAlive(): Promise<StreamAliveCheck> {
  const modules = SERVICE_CATALOG.map((c) => c.service);
  // One explicit pgrep line per module — deliberately NO shell variables
  // in the `bash -lc` string: `$`-expansion on some launch paths strips
  // loop vars to empty (wsl.exe command-line mangling), which turns every
  // verdict unparseable ("alive " with no service name). Pattern dots are
  // escaped so the checker shell's own cmdline (which carries the escaped
  // patterns and the plain names inside the echo labels) cannot
  // self-match either.
  const cmd = modules
    .map((m) => {
      const pattern = m.replace(/\./g, "\\.");
      return (
        `pgrep -f "python.* -m ${pattern}" >/dev/null 2>&1 ` +
        `&& echo "alive ${m}" || echo "dead ${m}";`
      );
    })
    .join(" ");
  const result = await runWslScript(cmd, "stream liveness pgrep pass");
  const alive: string[] = [];
  const dead: string[] = [];
  for (const line of result.stdout.split("\n")) {
    const verdict = /^([a-z]+) (\S+)$/.exec(line.trim());
    const state = verdict?.[1];
    const service = verdict?.[2];
    if (state === "alive" && service) alive.push(service);
    else if (state === "dead" && service) dead.push(service);
  }
  if (alive.length + dead.length !== modules.length) {
    return {
      alive,
      dead,
      error:
        result.exitCode === -1
          ? `WSL spawn failed: ${result.stderr.slice(-300)}`
          : `pgrep pass returned ${alive.length + dead.length}/${modules.length} verdicts (exit ${result.exitCode})`,
    };
  }
  return { alive, dead, error: null };
}

/** API-boot auto-start — when the UI starts, check the always-on stream
 *  and revive it if anything is down. An API server exit kills the
 *  keeper session and with it the whole stream, so every UI start is a
 *  potential all-dead state; a main_run.sh boot pre-starts the
 *  supervisor, and starting over a healthy stream is a documented no-op
 *  (stream_watchdog.sh's pgrep guard + max_parallel self-exit). One
 *  pgrep pass BOOT_CHECK_DELAY_MS after the server is listening —
 *  delayed because the predecessor's WSL session tears down
 *  asynchronously over the first seconds and an early pass could salute
 *  dying processes. Anything down spawns the same keeper as POST
 *  /stream-always; one follow-up pass confirms the revival (first beats
 *  land within ~60s) and re-fires the start if the first keeper already
 *  died. Bounded — no recurring supervision; a live watchdog owns that. */
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
      `[monitor/boot] stream liveness check failed (${check.error})` +
        (pass === "boot" ? " — one recheck in 90s" : ""),
    );
  } else if (check.dead.length === 0) {
    console.log(
      `[monitor/boot] all ${check.alive.length} always-on stream services have live processes — nothing to start`,
    );
    return;
  } else {
    console.warn(
      `[monitor/boot] stream services down at UI start: ${check.dead.join(", ")} — auto-starting the stream-always keeper`,
    );
    const start = startStreamAlwaysKeeper();
    if (start.alreadyRunning) {
      console.log(
        "[monitor/boot] keeper already up (this API server) — it revives the dead services on its own",
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
