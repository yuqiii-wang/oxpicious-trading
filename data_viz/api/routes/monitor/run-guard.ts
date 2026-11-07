/**
 * GET /stream/run/guard + POST /stream/run — the Run dialog's freq
 * auto-skip / parallel-services guard preview and the fire-and-forget
 * manual service spawn.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getReadPool,
} from "../../services/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import {
  isPythonProcessRunning,
  runPythonModule,
} from "../../services/py-runner.service.js";
import { serviceConfigFor } from "../../services/static-config.service.js";
import { FREQ_PERIOD_MS, SKIP_ANY_SUCCESS, STALE_AFTER_S, isRunnableModule } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Manual service run — POST /stream/run + GET /stream/run/guard
//
//  The UI's per-process Run button: spawns `python -m <service>` via the
//  shared py-runner (the service name IS the module path — every pipeline
//  registers by its entry module, scripts/*.sh run the same loop). Unless
//  force is set, the run is AUTO-SKIPPED when the service's configured
//  freq (service_config/ static tree) says the last successful exit is
//  still inside the current period — daily = last 24h, monthly = last 30d,
//  semiannually = last 183d, once = any success ever; always /
//  occasionally / on_demand never auto-skip. Force rerun bypasses the
//  guard. The spawn is FIRE-AND-FORGET (batch runs take hours): the POST
//  resolves as soon as the process is handed to WSL, the heartbeat table
//  carries the run's liveness/state from there, and the py-runner tag
//  registry dedupes concurrent spawns (GET /api/analysis/run-analysis/
//  status polls the same registry).
// ---------------------------------------------------------------------------

/** Result of the freq auto-skip evaluation for ONE service. */
interface FreqGuard {
  service: string;
  /** freq from the service_config/ static tree (default.json fallback). */
  freq: string;
  /** "within" = skip while last success < periodMs; "any_success" = skip
   *  after any success ever; "never" = no auto-skip. */
  skipRule: "within" | "any_success" | "never";
  /** The "within" window in ms (null for the other rules). */
  periodMs: number | null;
  /** Last successful exit (monitor.service_audit, is_normal_exit) — null
   *  when the service never exited cleanly. */
  lastSuccessAt: string | null;
  lastSuccessAgeS: number | null;
  /** The decision a force-less run would take right now. */
  wouldSkip: boolean;
}

/** Evaluate the freq auto-skip guard: the service's static-config freq vs
 *  its last successful exit in monitor.service_audit. */
async function evaluateFreqGuard(service: string): Promise<FreqGuard> {
  const cfg = serviceConfigFor(service);
  const period = FREQ_PERIOD_MS[cfg.freq] ?? null;
  const skipRule: FreqGuard["skipRule"] =
    period === null ? "never" : period === SKIP_ANY_SUCCESS ? "any_success" : "within";
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    "data_viz/api/routes/monitor/freq_guard_last_success",
    { service },
  );
  const rows = await pool.query<{ last_success_at: Date | null }>(sql, params);
  const lastSuccess = rows.rows[0]?.last_success_at ?? null;
  const ageMs = lastSuccess == null ? null : Date.now() - lastSuccess.getTime();
  const wouldSkip =
    skipRule === "any_success"
      ? lastSuccess != null
      : skipRule === "within" &&
        ageMs != null &&
        ageMs < (period as number);
  return {
    service,
    freq: cfg.freq,
    skipRule,
    periodMs: skipRule === "within" ? (period as number) : null,
    lastSuccessAt: lastSuccess ? lastSuccess.toISOString() : null,
    lastSuccessAgeS: ageMs == null ? null : Math.round(ageMs / 1000),
    wouldSkip,
  };
}

/** Process-id-tag of a UI-triggered service run (the py-runner registry
 *  key — dedupes concurrent spawns and serves the status poll). */
function serviceRunTag(service: string): string {
  return `service-run:${service}`;
}

// -- Cross-service parallel guard -----------------------------------------
//  The included/excluded_parallel_services lists (service_config/ static
//  tree — same files _common/service_heartbeat.py enforces at
//  registration) previewed by the Run dialog and enforced on POST
//  /stream/run. Entries are rooted dotted prefixes of a service name (a
//  scope "downloads", a sub-scope path "downloads.stock", or the full
//  service name); specificity = segment count, so on a conflict a service
//  name beats a sub-scope beats a scope, and a same-specificity tie
//  resolves to excluded (fail-safe). An empty included list = unrestricted;
//  a non-empty one is a whitelist. BOTH directions are evaluated — the
//  candidate's lists against every running service AND every running
//  service's lists against the candidate — so an exclusion holds
//  regardless of start order. "Running" mirrors the page's definition: a
//  heartbeat row with no stop record and a beat inside STALE_AFTER_S. A
//  DB problem never blocks (allowed=true + dbError, like the claim's
//  "skipped" state).

/** One pair violation: `running` is the live service, `owner` the config
 *  that blocked the pair (the candidate or the running service), `list` /
 *  `entry` the blocking list and its matched entry ("" = whitelist active,
 *  no match). */
interface ParallelConflict {
  running: string;
  owner: string;
  list: "included_parallel_services" | "excluded_parallel_services";
  entry: string;
}

/** Result of the parallel-services evaluation for ONE candidate. */
interface ParallelGuard {
  service: string;
  includedParallelServices: string[];
  excludedParallelServices: string[];
  /** Live services the lists were evaluated against. */
  runningChecked: number;
  allowed: boolean;
  /** Set (allowed forced true) when the DB was unreachable — the check
   *  was skipped, not passed. */
  dbError: string | null;
  conflicts: ParallelConflict[];
}

/** Segments of `entry` matching `other` as a rooted dotted prefix, else 0
 *  — the priority order (name > sub-scope > scope) IS the count. */
function matchSpecificity(entry: string, other: string): number {
  const e = entry.split(".");
  const o = other.split(".");
  return o.length >= e.length && e.every((seg, i) => o[i] === seg) ? e.length : 0;
}

/** Pair verdict — the blocking (list, entry) when `other` may NOT run in
 *  parallel with the owner of the two lists, else null. Mirrors
 *  _common/service_heartbeat.py _blocked_by — keep in sync. */
function blockedBy(
  included: string[],
  excluded: string[],
  other: string,
): { list: ParallelConflict["list"]; entry: string } | null {
  const best = (list: string[]) =>
    list.reduce(
      (acc, e) => Math.max(acc, matchSpecificity(e, other)),
      0,
    );
  const exc = best(excluded);
  const inc = best(included);
  if (exc > 0 && exc >= inc) {
    return { list: "excluded_parallel_services", entry: excluded.find((e) => matchSpecificity(e, other) === exc) ?? "" };
  }
  if (inc > 0) return null;
  if (included.length > 0) {
    return { list: "included_parallel_services", entry: "" };
  }
  return null;
}

/** Evaluate the parallel-services guard for ONE candidate against the
 *  currently live services (read replica — beats lag the primary by
 *  seconds at most; the 240s staleness window dwarfs it). */
async function evaluateParallelGuard(service: string): Promise<ParallelGuard> {
  const own = serviceConfigFor(service);
  let running: string[] = [];
  let dbError: string | null = null;
  try {
    const pool = getReadPool();
    const { sql, params } = renderSqlView(
      "data_viz/api/routes/monitor/parallel_guard_running",
      { service, stale_after_s: STALE_AFTER_S },
    );
    const rows = await pool.query<{ service: string }>(sql, params);
    running = rows.rows.map((r) => r.service);
  } catch (err) {
    dbError = String(err);
  }
  const conflicts: ParallelConflict[] = [];
  if (dbError == null) {
    for (const other of running) {
      const ownBlocked = blockedBy(
        own.included_parallel_services,
        own.excluded_parallel_services,
        other,
      );
      if (ownBlocked != null) {
        conflicts.push({ running: other, owner: service, ...ownBlocked });
        continue;
      }
      const otherCfg = serviceConfigFor(other);
      const otherBlocked = blockedBy(
        otherCfg.included_parallel_services,
        otherCfg.excluded_parallel_services,
        service,
      );
      if (otherBlocked != null) {
        conflicts.push({ running: other, owner: other, ...otherBlocked });
      }
    }
  }
  return {
    service,
    includedParallelServices: own.included_parallel_services,
    excludedParallelServices: own.excluded_parallel_services,
    runningChecked: running.length,
    // A DB problem forces the check skipped — allowed, but flagged.
    allowed: dbError != null || conflicts.length === 0,
    dbError,
    conflicts,
  };
}

/** Human sentence for one conflict — the POST's refusal message. */
function conflictSentence(c: ParallelConflict): string {
  const detail =
    c.entry !== ""
      ? `${c.owner}'s ${c.list} entry '${c.entry}'`
      : `${c.owner}'s ${c.list} whitelist (service not listed)`;
  return `${c.running} (running) vs ${detail}`;
}

/** GET /api/monitor/stream/run/guard?service=… — the freq auto-skip +
 *  parallel-services evaluation the Run dialog previews (freq, last
 *  successful exit, whether a force-less run would be skipped right now;
 *  plus the cross-service parallel verdict against the live services). */
router.get("/stream/run/guard", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  if (!service || !/^[A-Za-z0-9_.-]+$/.test(service) || !isRunnableModule(service)) {
    res.status(400).json({
      success: false,
      error: `service must be a runnable dotted module path, got '${service}'`,
    });
    return;
  }
  try {
    const [freq, parallel] = await Promise.all([
      evaluateFreqGuard(service),
      evaluateParallelGuard(service),
    ]);
    res.json({ success: true, ...freq, parallel });
  } catch (err) {
    console.error("[monitor/stream/run/guard] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** POST /api/monitor/stream/run { service, force?, args? } — spawn
 *  `python -m <service> [args...]` (fire-and-forget) unless a guard skips
 *  it: a force-less run inside the freq period auto-skips (force rerun
 *  bypasses), and a parallel-services conflict with a live service is
 *  refused outright (NOT bypassable by force — it is a data-integrity
 *  constraint; edit the config to lift it). Deduped by process-id-tag (a
 *  concurrent UI run of the SAME service resolves with alreadyRunning —
 *  args are NOT part of the tag, so two steps sharing a module dedupe
 *  onto one another); the module's own max_parallel + parallel-services
 *  registration claims cover non-UI duplicates (cron + manual),
 *  self-exiting past the cap or on a conflict. */
router.post("/stream/run", async (req: Request, res: Response) => {
  const service = typeof req.body?.service === "string" ? req.body.service.trim() : "";
  const force = req.body?.force === true;
  const args = Array.isArray(req.body?.args) ? req.body.args : [];
  if (
    args.length > 8 ||
    args.some(
      (a: unknown) =>
        typeof a !== "string" || a === "" || a.length > 64 || !/^[\w.,:=@+-]+$/.test(a),
    )
  ) {
    res.status(400).json({
      success: false,
      error:
        "args must be <= 8 short CLI tokens (letters/digits/_ . , : = @ + -) — they are joined into the bash -lc command verbatim",
    });
    return;
  }
  if (!service || !/^[A-Za-z0-9_.-]+$/.test(service) || !isRunnableModule(service)) {
    res.status(400).json({
      success: false,
      error: `service must be a runnable dotted module path, got '${service}'`,
    });
    return;
  }
  const tag = serviceRunTag(service);
  if (isPythonProcessRunning(tag)) {
    res.json({
      success: true,
      started: true,
      alreadyRunning: true,
      service,
      force,
      message: `${service} is already running (this UI spawn) — deduped, no new process`,
    });
    return;
  }
  if (!force) {
    try {
      const guard = await evaluateFreqGuard(service);
      if (guard.wouldSkip) {
        res.json({
          success: true,
          started: false,
          skipped: true,
          ...guard,
          message: `skipped — last successful exit ${guard.lastSuccessAt} is inside the ${guard.freq} period; enable force rerun to run anyway`,
        });
        return;
      }
    } catch (err) {
      console.error("[monitor/stream/run] guard error:", err);
      res.status(500).json({ success: false, error: String(err) });
      return;
    }
  }
  // Parallel-services conflict — refused on every run (force does not
  // bypass it; a DB problem inside the guard admits the run instead).
  try {
    const parallel = await evaluateParallelGuard(service);
    if (!parallel.allowed) {
      console.log(
        `[monitor/stream/run] ${service} blocked by parallel-services guard: ` +
          parallel.conflicts.map(conflictSentence).join("; "),
      );
      res.json({
        success: true,
        started: false,
        blocked: true,
        parallel,
        message: `blocked — parallel-services conflict: ${parallel.conflicts
          .map(conflictSentence)
          .join("; ")}; edit the service_config lists to lift it`,
      });
      return;
    }
  } catch (err) {
    console.error("[monitor/stream/run] parallel guard error:", err);
    res.status(500).json({ success: false, error: String(err) });
    return;
  }
  // Fire-and-forget: the POST resolves immediately; the runner's tag
  // registry tracks the process until exit (logged here).
  void runPythonModule(service, args, { processIdTag: tag }).then((r) => {
    if (r.success) {
      console.log(`[monitor/stream/run] ${service} ${args.join(" ")} exited 0`);
    } else {
      console.error(
        `[monitor/stream/run] ${service} ${args.join(" ")} exited ${r.exitCode}: ${r.stderr.slice(-500)}`,
      );
    }
  });
  res.json({
    success: true,
    started: true,
    service,
    force,
    message: `spawned python -m ${service}${args.length ? ` ${args.join(" ")}` : ""}${
      force ? " (force rerun)" : ""
    } — first heartbeat beat within ~60s`,
  });
});

export default router;
