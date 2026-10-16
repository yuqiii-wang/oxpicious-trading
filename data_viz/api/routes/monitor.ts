/**
 * Monitoring API routes — service process liveness + run audit + the
 * external-resource request audit aggregates.
 *
 *   GET /api/monitor/stream
 *     → heartbeat state of the monitored services (scope + sub_scope per
 *       row, stop tracking: stopReason/isNormalExit/stoppedAt, and the
 *       per-service live_parallel from the heartbeat row + max_parallel
 *       from the static JSON config) plus
 *       the watchdog restart trail; `processes` carries the heartbeat
 *       rows of every OTHER registered pipeline (batch builds /
 *       downloads / analyses / llm runs — last-run state, annotated with
 *       the supposed-run freq/schedule from the service_config/ static
 *       tree; the UI expands each row to its run history via
 *       /stream/runs below) PLUS every registered service that never ran
 *       (config-tree entries without a heartbeat row, status "never" —
 *       so the full-history catalog covers the whole registration).
 *
 *   GET /api/monitor/stream/runs?service=...&limit=10
 *     → run audit history of ONE pipeline process (monitor.service_audit).
 *
 *   GET /api/monitor/stream/runs/log?service=...&date=YYYY-MM-DD[&tail=]
 *     → ONE run's component log: the tail of logs/<date>/<service>.log —
 *       the ONE file _common/log_setup wrote for that service that day,
 *       named by the service's dotted module path (one date one service
 *       one log file). Pre-refactor dates kept per-component file names,
 *       so a miss falls back to the legacy lookup: candidate component
 *       names parsed from the service's module source (DataPipeline
 *       `component` attrs + setup_logging(...) / setup_logger(...)
 *       names, __main__.py first;
 *       the dotted name's last segment as fallback), first that exists in
 *       the date's directory wins. found=false carries the candidates
 *       tried, for the UI's no-log-on-record state.
 *
 *   GET /api/monitor/stream/run/guard?service=...
 *     → the freq auto-skip + parallel-services evaluation for ONE
 *       pipeline process: its service_config freq, last successful exit
 *       (monitor.service_audit is_normal_exit), and whether a force-less
 *       run would be skipped right now (daily = 24h / monthly = 30d /
 *       semiannually = 183d / once = any success; always / occasionally /
 *       on_demand never skip), plus the cross-service parallel verdict
 *       (included/excluded_parallel_services vs the live services, both
 *       directions). The Run dialog previews this before POSTing.
 *
 *   POST /api/monitor/stream/run  { service, force?, args? }
 *     → fire-and-forget spawn of `python -m <service> [args...]` via the
 *       shared py-runner (the service name IS the module path) — unless a
 *       force-less run lands inside the freq period above, which auto-
 *       skips (force rerun bypasses), or a parallel-services conflict
 *       with a live service, which is refused outright (force does NOT
 *       bypass it — edit the service_config lists instead). Deduped per
 *       service by process-id-tag; liveness then lives in the heartbeat
 *       table (first beat ~60s) and the run in service_audit like any
 *       other registration.
 *
 *   POST /api/monitor/orchestration/run  { file, dryRun? }
 *     → fire-and-forget spawn of the Orchestration config tab's tier run:
 *       `bash scripts/run_steps.sh scripts/orchestration/<file>` — the
 *       shared serial runner the scripts/<tier>.sh guards delegate to, so
 *       a manual tier run skips the trading-day/time gate. Steps spawn
 *       one by one and heartbeat onto Pipeline processes as they start;
 *       dryRun=true echoes the plan without executing (smoke only).
 *
 *   POST /api/monitor/health-check
 *     → force-trigger `python -m database.monitoring` (the point-in-time
 *       alert check: replica sync, replay backlog, slot WAL retention,
 *       pg_wal dir size vs max_wal_size, downloads-stream liveness) and
 *       return its [ok] / [ALERT] lines + the health verdict. The UI's
 *       refresh button on the Service Monitor page fires this.
 *
 *   POST /api/monitor/stream-always
 *     → bring the always-on stream up: the idempotent watchdog starter
 *       (scripts/stream_watchdog.sh) then the staggered poller starter
 *       (scripts/stream_always.sh — nohup the five downloads.stream.*
 *       pollers, sleep 1 apart) inside a HIDDEN keeper wsl.exe session
 *       that parks on sleep infinity and holds the services alive (a WSL
 *       session's nohup'd children die with its wsl.exe client, so the
 *       client — held by this API server — must stay connected; an API
 *       restart takes the stream down, the button is the recovery).
 *       Spawned through the shared py-runner, fire-and-forget, deduped
 *       by process-id-tag. The UI's "Start stream always" button fires
 *       this.
 *
 *   GET /api/monitor/ext-resources/stats?days=30
 *     → every monitor.external_resource_stats row in the trailing window
 *       (grain: service × status_code × date — the per-day aggregation of
 *       the external request audit, computed by python -m
 *       database.external_resource_stats) + the distinct date / service
 *       axes for the trend chart. Serves the External Resources tab's
 *       trend + per-date breakdown; the per-row drill-down to raw audit
 *       rows is /ext-resources/audit below.
 *
 *   GET /api/monitor/ext-resources/audit?service=...&date=YYYY-MM-DD
 *                                      [&status=<code>|none] [&limit=]
 *     → raw monitor.external_resource_audit rows for ONE (service, date)
 *       bucket, optionally narrowed to one status_code bucket ("none" =
 *       the no-response transport-error bucket). resource_type / sub_type
 *       arrive DECODED (label-code contract: decode at the API boundary;
 *       sub_type's domain is selected by resource_type).
 *
 *   POST /api/monitor/ext-resources/refresh
 *     → re-run python -m database.external_resource_stats — the per-day
 *       aggregation over monitor.external_resource_audit that the stats
 *       endpoint above reads (COMPUTED table: a day shows up only after
 *       this runs). The UI's refresh button on the External Resources
 *       page fires this. Deduped by process-id-tag.
 *
 *   GET /api/monitor/ext-resources/refresh/status
 *     → running-state of that refresh process-id-tag, so the button can
 *       restore its spinner after a page refresh mid-run.
 *
 *   GET /api/monitor/ext-resources/configs
 *     → the whole external-request config inventory — per-(service,
 *       request_type) pacing / anti-bot config (sleep cooldown, jitter,
 *       poll cadence, retries/backoff, timeouts, toggles) from the
 *       static JSON tree (database/sql/monitoring/static/
 *       external_request_config/<dotted.service.path>.json via the
 *       shared static-config service). Tiny by construction (one row per
 *       request type per service), so the tab fetches it whole once: the
 *       breakdown table shows each service's config count and expands to
 *       the config rows alongside the raw audit sample. resource_type
 *       arrives DECODED like the audit rows.
 *
 *   GET /api/monitor/src-data-audit?sec_type=index|etf|stock|all&q=&limit=
 *     → the per-security source-data coverage audit
 *       (monitor.src_data_audit — n_days / first_date per (sec_type, code),
 *       upserted by the classification + ETF builds; last_date re-derived
 *       per code from the stats source tables at query time so a refresh
 *       always reflects the data that actually arrived).
 *       Serves the Source Data tab's filterable table (sec_type dropdown +
 *       code/name search); name is JOINed in from stats.sec_classification.
 *
 * Reads monitor.service_heartbeat / monitor.service_audit /
 * monitor.external_resource_stats / monitor.external_resource_audit /
 * monitor.src_data_audit on
 * the read replica — beats are upserted every ~60s and the stale
 * threshold is 240s, so ordinary replica lag never flips a live service
 * to stale. The watchdog restart trail is merged into service_audit (a
 * restart closes the dead life with a 'watchdog restart: …' exit_reason;
 * recentRestarts is a window over those rows).
 */
import { existsSync } from "fs";
import { readFile, readdir } from "fs/promises";
import path from "path";
import { Router, type Request, type Response } from "express";
import {
  getReadPool,
  queryRows,
} from "../services/db.service.js";
import { decodeLabel, encodeLabelParam, LABEL_CODE } from "../services/label-codes.js";
import {
  getPythonProcessStatus,
  isPythonProcessRunning,
  runPythonModule,
  runWslScript,
} from "../services/py-runner.service.js";
import {
  maxParallelFor,
  readDefaultExtRequestConfig,
  readExternalRequestConfig,
  readServiceConfig,
  serviceConfigFor,
  type ExtRequestConfigRow,
} from "../services/static-config.service.js";

const router = Router();

// Mirror of _common/service_heartbeat.py (MONITORED_SERVICES — unique
// service name → scope/sub_scope — plus STALE_AFTER_S); keep in sync. The
// heartbeat table only tells us which services HAVE beaten, not which
// SHOULD, so the expected set lives here.
const WATCHDOG_SERVICE = "database.stream_watchdog";
const STALE_AFTER_S = 240;
const SERVICE_CATALOG: ReadonlyArray<{
  service: string;
  scope: string;
  subScope: string | null;
}> = [
  { service: "downloads.stream.sse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.szse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.bse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.csindex.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.cnindex.price", scope: "downloads", subScope: "stream" },
  // DB health sampler — always-on like the pollers (stream sub-scope).
  { service: "database.db_health", scope: "database", subScope: "stream" },
  { service: WATCHDOG_SERVICE, scope: "database", subScope: "monitoring" },
];

interface BeatRow {
  service: string;
  pid: number;
  started_at: Date;
  beat_at: Date;
  beat_age_s: number;
  scope: string;
  sub_scope: string | null;
  stop_reason: string | null;
  is_normal_exit: boolean | null;
  stopped_at: Date | null;
  live_parallel: number;
}

/** A watchdog restart, read from the merged trail: the dead life's row in
 *  monitor.service_audit, closed with a 'watchdog restart: …' exit_reason
 *  when the watchdog spawned the fresh process. */
interface RestartRow {
  service: string;
  ended_at: Date;
  pid: number;
  exit_reason: string;
  scope: string | null;
  sub_scope: string | null;
}

interface AuditLifeRow {
  id: string;
  service: string;
  scope: string;
  sub_scope: string | null;
  pid: number;
  started_at: Date;
  ended_at: Date | null;
  exit_reason: string | null;
  is_normal_exit: boolean | null;
}

export interface StreamServiceStatus {
  service: string;
  scope: string;
  subScope: string | null;
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  status: "live" | "stale" | "stopped" | "down";
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  restarts24h: number;
  /** monitor.service_heartbeat.live_parallel / the max_parallel cap from
   *  the static JSON config (a registration auto-exits past the cap). */
  liveParallel: number | null;
  maxParallel: number | null;
}

/** Non-stream pipeline process (batch builds / downloads / analyses /
 * llm / live runs): the heartbeat table's LAST-RUN state. "stale" means
 * the beat went quiet with no stop record — died without a record until
 * the watchdog's orphan pass marks it stopped. "never" marks a registered
 * service (service_config/ static tree) with NO heartbeat row at all —
 * it has never run. freq / schedule come from the service_config/ static
 * tree (how often the service is SUPPOSED to run — scripts/*.sh cadence;
 * on_demand for services without their own file). */
export interface ProcessStatus {
  service: string;
  scope: string;
  subScope: string | null;
  /** null only on "never" rows (no process ever). */
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  status: "running" | "stale" | "stopped" | "never";
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  liveParallel: number | null;
  maxParallel: number | null;
  freq: string;
  /** The freq's auto-skip window in ms (FREQ_PERIOD_MS) — null when the
   *  freq has no checkable period (always / occasionally / on_demand /
   *  once). The UI ambers a Last beat older than this against the
   *  supposed cadence. */
  freqPeriodMs: number | null;
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
}

export interface MonitorStreamResponse {
  success: true;
  generatedAt: string;
  staleAfterS: number;
  watchdog: StreamServiceStatus | null;
  services: StreamServiceStatus[];
  processes: ProcessStatus[];
  recentRestarts: Array<{
    service: string;
    scope: string | null;
    subScope: string | null;
    /** When the watchdog restarted the service = the dead life's ended_at. */
    restartedAt: string;
    /** The pid the restart replaced (the dead life's pid). */
    pid: number;
    reason: string;
  }>;
}

function toStatus(
  expected: { service: string; scope: string; subScope: string | null },
  beat: BeatRow | undefined,
  restarts24h: Map<string, number>,
): StreamServiceStatus {
  if (!beat) {
    return {
      service: expected.service,
      scope: expected.scope,
      subScope: expected.subScope,
      pid: null,
      startedAt: null,
      beatAt: null,
      beatAgeS: null,
      status: "down",
      stopReason: null,
      isNormalExit: null,
      stoppedAt: null,
      restarts24h: restarts24h.get(expected.service) ?? 0,
      liveParallel: 0,
      maxParallel: maxParallelFor(expected.service), // static JSON config
    };
  }
  const status: StreamServiceStatus["status"] =
    beat.stop_reason != null
      ? "stopped"
      : beat.beat_age_s <= STALE_AFTER_S
        ? "live"
        : "stale";
  return {
    service: expected.service,
    scope: beat.scope || expected.scope,
    subScope: beat.sub_scope ?? expected.subScope,
    pid: beat.pid,
    startedAt: beat.started_at.toISOString(),
    beatAt: beat.beat_at.toISOString(),
    beatAgeS: beat.beat_age_s,
    status,
    stopReason: beat.stop_reason,
    isNormalExit: beat.is_normal_exit,
    stoppedAt: beat.stopped_at ? beat.stopped_at.toISOString() : null,
    restarts24h: restarts24h.get(expected.service) ?? 0,
    liveParallel: beat.live_parallel,
    maxParallel: maxParallelFor(expected.service), // static JSON config
  };
}

/** GET /api/monitor/stream/runs?service=...&limit=10 — run audit history
 *  of ONE pipeline process (the pipeline table's expandable rows). Full
 *  per-service history with its own limit — the UI dropped the global
 *  recent-runs table because the shared window crowded each service out
 *  (~50 rows across ~30 registered services ≈ 2 runs each). */
router.get("/stream/runs", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  const limit = Math.min(Math.max(Number(req.query.limit) || 10, 1), 100);
  if (!service) {
    res.status(400).json({ success: false, error: "query param 'service' is required" });
    return;
  }
  try {
    const pool = getReadPool();
    const rows = await pool.query<AuditLifeRow>(
      `SELECT id, service, scope, sub_scope, pid, started_at, ended_at,
              exit_reason, is_normal_exit
       FROM monitor.service_audit
       WHERE service = $1
       ORDER BY started_at DESC
       LIMIT $2`,
      [service, limit],
    );
    res.json({
      success: true,
      service,
      runs: rows.rows.map((r) => ({
        id: r.id,
        service: r.service,
        scope: r.scope,
        subScope: r.sub_scope,
        pid: r.pid,
        startedAt: r.started_at.toISOString(),
        endedAt: r.ended_at ? r.ended_at.toISOString() : null,
        exitReason: r.exit_reason,
        isNormalExit: r.is_normal_exit,
      })),
    });
  } catch (err) {
    console.error("[monitor/stream/runs] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  Run logs — GET /stream/runs/log?service=…&date=YYYY-MM-DD
//
//  One pipeline run's log: _common/log_setup writes ONE file per
//  (date, service) — logs/<local start date>/<service>.log, the dotted
//  service name — so the direct lookup is just the service path. Dates
//  before that convention kept per-component file names, so a miss falls
//  back to parsing candidate component names from the service's module
//  source (the audit row does not record them).
// ---------------------------------------------------------------------------

/** LEGACY candidate log component names for ONE service (dates before the
 *  per-service file naming), parsed from its module source in priority
 *  order: import-time setup_logger(...) names (these keyed the legacy
 *  file), then the entry declarations (DataPipeline `component` attrs,
 *  setup_logging(...) names) — __main__.py before the rest of the
 *  package (capped); the dotted name's last segment is the final
 *  fallback. */
async function logComponentCandidates(service: string): Promise<string[]> {
  const rel = service.split(".").join(path.sep);
  const modFile = `${path.join(REPO_ROOT, rel)}.py`;
  const modDir = path.join(REPO_ROOT, rel);
  const mainFiles: string[] = [];
  const otherFiles: string[] = [];
  const collectPy = async (dir: string, isModuleRoot: boolean): Promise<void> => {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__pycache__") await collectPy(p, false);
      } else if (entry.name.endsWith(".py")) {
        if (isModuleRoot && entry.name === "__main__.py") mainFiles.push(p);
        else otherFiles.push(p);
      }
    }
  };
  if (existsSync(modFile)) mainFiles.push(modFile);
  if (existsSync(modDir)) await collectPy(modDir, true);

  // First declaration per pattern wins — the process component is declared
  // once at the entry point; deeper files carry internal-step loggers.
  // setup_logger names outrank the entry declarations: pre-refactor the
  // FILE was keyed by the first setup call in process import order, and
  // for the downloader packages that is a package-level setup_logger(...)
  // in a _config.py imported at the top of __main__ — the entry's own
  // setup_logging(...) name only became the logger prefix. Without this
  // priority a generic entry name (composition) steals another service's
  // file (builds.index.composition's composition.log) on shared dates.
  const rxComponent = /\bcomponent(?:\s*:\s*\w+)?\s*=\s*"([^"]+)"/;
  const rxSetup = /setup_logging\(\s*"([^"]+)"/;
  const rxSetupLogger = /setup_logger\(\s*"([^"]+)"/;
  const importTimeNames: string[] = [];
  const entryNames: string[] = [];
  const collect = async (files: string[], cap?: number): Promise<void> => {
    for (const f of files.slice(0, cap ?? files.length)) {
      try {
        const src = await readFile(f, "utf8");
        const first = (rx: RegExp, into: string[]): void => {
          const m = rx.exec(src);
          if (m && m[1] !== "" && !into.includes(m[1])) into.push(m[1]);
        };
        first(rxSetupLogger, importTimeNames);
        first(rxComponent, entryNames);
        first(rxSetup, entryNames);
      } catch {
        // unreadable source — skip the file
      }
    }
  };
  await collect(mainFiles);
  await collect(otherFiles, 60);
  const names = [...importTimeNames, ...entryNames];
  const last = service.split(".").at(-1);
  if (last && last !== "" && !names.includes(last)) names.push(last);
  return names;
}

/** GET /api/monitor/stream/runs/log?service=…&date=YYYY-MM-DD[&tail=] —
 *  the tail of ONE service's log for ONE local date: the service file
 *  _common/log_setup wrote (logs/<date>/<service>.log), falling back to
 *  the legacy per-component names for pre-refactor dates. `file` is
 *  relative to the repo root; a huge log arrives truncated to the last
 *  `tail` lines with `totalLines` / `sizeBytes` for the notice. */
router.get("/stream/runs/log", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  const date = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const tail = Math.min(Math.max(Number(req.query.tail) || 2000, 50), 20000);
  if (!service || !/^[A-Za-z0-9_.-]+$/.test(service) || !isRunnableModule(service)) {
    res.status(400).json({
      success: false,
      error: `service must be a runnable dotted module path, got '${service}'`,
    });
    return;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    res.status(400).json({
      success: false,
      error: "query param 'date' (YYYY-MM-DD) is required",
    });
    return;
  }
  try {
    const dayDir = path.join(REPO_ROOT, "logs", date);
    // The direct per-service file first (one date one service one log
    // file); the legacy per-component candidate scan only on a miss.
    let file: string | null = null;
    let candidates: string[] = [];
    const direct = path.join(dayDir, `${service}.log`);
    if (existsSync(direct)) {
      file = direct;
      candidates = [service];
    } else {
      candidates = await logComponentCandidates(service);
      for (const c of candidates) {
        const f = path.join(dayDir, `${c}.log`);
        if (existsSync(f)) {
          file = f;
          break;
        }
      }
    }
    if (file == null) {
      res.json({
        success: true,
        service,
        date,
        component: null,
        file: null,
        candidates,
        found: false,
        content: "",
        truncated: false,
        totalLines: 0,
        sizeBytes: 0,
      });
      return;
    }
    const component = path.basename(file, ".log");
    const raw = await readFile(file, "utf8");
    const sizeBytes = Buffer.byteLength(raw, "utf8");
    const lines = raw.split("\n");
    if (lines.at(-1) === "") lines.pop();
    const totalLines = lines.length;
    const truncated = totalLines > tail;
    const content = (truncated ? lines.slice(-tail) : lines).join("\n");
    res.json({
      success: true,
      service,
      date,
        component,
        file: path.relative(REPO_ROOT, file).split(path.sep).join("/"),
      candidates,
      found: true,
      content,
      truncated,
      totalLines,
      sizeBytes,
    });
  } catch (err) {
    console.error("[monitor/stream/runs/log] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

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

/** Freq → auto-skip window in ms. `null` = never auto-skip (ad-hoc /
 *  continuous); `Infinity`-marker SKIP_ANY_SUCCESS = one success ever
 *  satisfies the freq. Unknown freq values fall back to never-skip. */
const SKIP_ANY_SUCCESS = Number.POSITIVE_INFINITY;
const FREQ_PERIOD_MS: Record<string, number | null> = {
  always: null,
  daily: 24 * 3600_000,
  monthly: 30 * 24 * 3600_000,
  semiannually: 183 * 24 * 3600_000,
  once: SKIP_ANY_SUCCESS,
  occasionally: null,
  on_demand: null,
};

/** The finite window of ``freq`` (null = no checkable period: the
 *  no-window freqs, "once" whose any-success rule never lapses, and
 *  unknown values). JSON-serializable form of FREQ_PERIOD_MS — the
 *  pipeline-process rows carry it so the UI can amber an overdue
 *  Last beat against the supposed cadence. */
function freqPeriodMsFor(freq: string): number | null {
  const period = FREQ_PERIOD_MS[freq] ?? null;
  return period != null && Number.isFinite(period) ? period : null;
}

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
  const rows = await pool.query<{ last_success_at: Date | null }>(
    `SELECT max(ended_at) AS last_success_at
     FROM monitor.service_audit
     WHERE service = $1 AND is_normal_exit AND ended_at IS NOT NULL`,
    [service],
  );
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
    const rows = await pool.query<{ service: string }>(
      `SELECT service FROM monitor.service_heartbeat
       WHERE service <> $1 AND stop_reason IS NULL
         AND beat_at > now() - make_interval(secs => $2)`,
      [service, STALE_AFTER_S],
    );
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

/** The repo root as the OS sees it (api/routes → api → data_viz → root) —
 *  module-existence check so a typo'd / non-module service name is a 400
 *  instead of an instant argparse-style failure after the spawn. */
const REPO_ROOT = path.resolve(import.meta.dirname ?? __dirname, "..", "..", "..");

/** A service is runnable iff `python -m <service>` has an entry: the dotted
 *  name maps to a package with __main__.py or a single-file module. */
function isRunnableModule(service: string): boolean {
  const rel = service.split(".").join(path.sep);
  return (
    existsSync(path.join(REPO_ROOT, rel, "__main__.py")) ||
    existsSync(path.join(REPO_ROOT, `${rel}.py`))
  );
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

// ---------------------------------------------------------------------------
//  Orchestration tier run — scripts/run_steps.sh (the Orchestration
//  config tab's title Run button)
// ---------------------------------------------------------------------------

/** POST /api/monitor/orchestration/run { file, dryRun? } — fire-and-forget
 *  spawn of the shared serial runner over ONE orchestration step file:
 *  `bash scripts/run_steps.sh scripts/orchestration/<file>` (the exact
 *  delegation the scripts/<tier>.sh guards end in — calling run_steps.sh
 *  directly IS the manual run, no trading-day/time gate). The steps spawn
 *  one by one and each registers its own heartbeat, so the tier's steps
 *  show up on the Pipeline processes table as they start. dryRun=true
 *  passes the runner's own DRY_RUN=1 knob (echoes the plan, executes
 *  nothing) — verification / smoke only, never surfaced in the UI.
 *  Deduped per tier by process-id-tag; the wsl.exe client is held for the
 *  whole tier (same keeper mechanics as the stream starters). */
router.post("/orchestration/run", (req: Request, res: Response) => {
  const file = typeof req.body?.file === "string" ? req.body.file.trim() : "";
  const dryRun = req.body?.dryRun === true;
  if (!/^[A-Za-z0-9_-]+\.json$/.test(file)) {
    res.status(400).json({
      success: false,
      error: `file must be an orchestration step file name like builds_daily.json, got '${file}'`,
    });
    return;
  }
  // Forward slashes: the path is joined into the WSL bash command, where
  // a Windows separator would not resolve.
  const jsonRel = `scripts/orchestration/${file}`;
  if (!existsSync(path.join(REPO_ROOT, "scripts", "orchestration", file))) {
    res.status(400).json({
      success: false,
      error: `no orchestration step file ${jsonRel}`,
    });
    return;
  }
  const tier = file.replace(/\.json$/, "");
  const tag = `orchestration-run:${tier}`;
  if (isPythonProcessRunning(tag)) {
    res.json({
      success: true,
      started: true,
      alreadyRunning: true,
      tier,
      message: `${tier} is already running (this UI spawn) — deduped, no new process`,
    });
    return;
  }
  const cmd = `${dryRun ? "DRY_RUN=1 " : ""}bash scripts/run_steps.sh ${jsonRel}`;
  void runWslScript(cmd, `run_steps.sh ${file}`, { processIdTag: tag }).then((r) => {
    if (r.success) {
      console.log(`[monitor/orchestration/run] ${file} exited 0`);
    } else {
      console.error(
        `[monitor/orchestration/run] ${file} exited ${r.exitCode}: ${r.stderr.slice(-500)}`,
      );
    }
  });
  res.json({
    success: true,
    started: true,
    tier,
    message: `spawned ${cmd}${dryRun ? " (dry run)" : ""} — steps start one by one, each heartbeating onto Pipeline processes (~60s)`,
  });
});

interface ExtStatRow {
  service: string;
  status_code: number | null;
  date: string;
  request_count: number;
  failed_count: number;
  avg_duration_ms: number | null;
  max_duration_ms: number | null;
}

interface ExtAuditRow {
  id: string;
  resource_type: number;
  sub_type: number;
  url: string;
  status_code: number | null;
  is_failed: boolean;
  failure_reason: string | null;
  duration_ms: number | null;
  created_at: Date;
}

export interface ExtResourceStatEntry {
  service: string;
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

export interface ExtResourceAuditEntry {
  id: string;
  /** Decoded ext_resource label: http | llm. */
  resourceType: string | null;
  /** Decoded ext_http_source (http rows) / ext_llm_provider (llm rows) label. */
  subType: string | null;
  url: string;
  statusCode: number | null;
  isFailed: boolean;
  failureReason: string | null;
  durationMs: number | null;
  createdAt: string;
}

export interface ExtRequestConfigEntry {
  service: string;
  requestType: string;
  /** Decoded ext_resource label: http | llm. */
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
  notes: string | null;
  sourceRef: string;
}

export interface ExtRequestConfigsResponse {
  success: true;
  generatedAt: string;
  rows: ExtRequestConfigEntry[];
  /** The default.json fallback rows (repo-wide 60s anti-bot cooldown),
   *  for services without their own file — service is the pseudo-name
   *  "default"; substitute the requesting service client-side. */
  defaultRows: ExtRequestConfigEntry[];
}

/** GET /api/monitor/ext-resources/stats?days=30 — the computed per-day
 *  external-request aggregates (python -m
 *  database.external_resource_stats maintains the table; this endpoint is
 *  a read-only window over it). Small by construction — days × services ×
 *  statuses rows — so the tab fetches the whole window once and pivots
 *  client-side. */
router.get("/ext-resources/stats", async (req: Request, res: Response) => {
  const days = Math.min(Math.max(Number(req.query.days) || 30, 1), 365);
  try {
    const pool = getReadPool();
    const stats = await pool.query<ExtStatRow>(
      `SELECT service, status_code, date, request_count, failed_count,
              avg_duration_ms, max_duration_ms
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ($1::int - 1))
       ORDER BY date, service, status_code NULLS FIRST`,
      [days],
    );
    const computed = await pool.query<{ computed_at: Date | null }>(
      `SELECT max(computed_at) AS computed_at
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ($1::int - 1))`,
      [days],
    );
    const rows: ExtResourceStatEntry[] = stats.rows.map((r) => ({
      service: r.service,
      statusCode: r.status_code,
      date: r.date,
      requestCount: r.request_count,
      failedCount: r.failed_count,
      avgDurationMs: r.avg_duration_ms == null ? null : Number(r.avg_duration_ms),
      maxDurationMs: r.max_duration_ms,
    }));
    const body: ExtResourceStatsResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      computedAt: computed.rows[0]?.computed_at
        ? computed.rows[0].computed_at.toISOString()
        : null,
      dates: [...new Set(rows.map((r) => r.date))].sort(),
      services: [...new Set(rows.map((r) => r.service))].sort(),
      rows,
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ext-resources/stats] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/monitor/ext-resources/audit?service=&date=&status=&limit= —
 *  raw audit rows behind ONE (service, date) aggregate bucket. status
 *  = "none" selects the no-response bucket (status_code IS NULL); a
 *  numeric value narrows to that HTTP status; omitted returns the
 *  service+date rows across all statuses. */
router.get("/ext-resources/audit", async (req: Request, res: Response) => {
  const service = typeof req.query.service === "string" ? req.query.service.trim() : "";
  const date = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const statusRaw = typeof req.query.status === "string" ? req.query.status.trim() : "";
  const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
  if (!service || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    res.status(400).json({
      success: false,
      error: "query params 'service' and 'date' (YYYY-MM-DD) are required",
    });
    return;
  }
  // "none" → status_code IS NULL (transport errors); digits → that status;
  // anything else non-empty is a bad param, not a silent "all".
  let statusClause = "";
  let statusParam: number | null = null;
  if (statusRaw === "none") {
    statusClause = " AND status_code IS NULL";
  } else if (statusRaw !== "") {
    if (!/^\d{1,3}$/.test(statusRaw)) {
      res.status(400).json({
        success: false,
        error: "query param 'status' must be a numeric HTTP status or 'none'",
      });
      return;
    }
    statusParam = Number(statusRaw);
    statusClause = " AND status_code = $3";
  }
  try {
    const pool = getReadPool();
    const params: Array<string | number | number> = [service, date];
    if (statusParam != null) params.push(statusParam);
    params.push(limit);
    const rows = await pool.query<ExtAuditRow>(
      `SELECT id, resource_type, sub_type, url, status_code, is_failed,
              failure_reason, duration_ms, created_at
       FROM monitor.external_resource_audit
       WHERE service = $1 AND date = $2::date${statusClause}
       ORDER BY created_at DESC, id DESC
       LIMIT $${params.length}`,
      params,
    );
    res.json({
      success: true,
      service,
      date,
      entries: rows.rows.map((r) => ({
        id: r.id,
        resourceType: decodeLabel("ext_resource", r.resource_type),
        subType: decodeLabel(
          r.resource_type === 0 ? "ext_http_source" : "ext_llm_provider",
          r.sub_type,
        ),
        url: r.url,
        statusCode: r.status_code,
        isFailed: r.is_failed,
        failureReason: r.failure_reason,
        durationMs: r.duration_ms,
        createdAt: r.created_at.toISOString(),
      })),
    });
  } catch (err) {
    console.error("[monitor/ext-resources/audit] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** Process-id-tag of the ext-resource stats refresh run — one fixed tag:
 *  there is exactly one logical refresh (whole-table incremental), so
 *  double clicks / tabs dedupe onto it. */
const EXT_RES_STATS_TAG = "monitor:ext-res-stats";

/** POST /api/monitor/ext-resources/refresh — re-run the per-day stats
 *  aggregation (python -m database.external_resource_stats) so the
 *  trailing window picks up the audit rows written since the last run.
 *  The stats table is COMPUTED — the stats endpoint above never
 *  aggregates on read — so a day appears only after this has run. The
 *  incremental run rebuilds just the stale dates and exits in seconds. */
router.post("/ext-resources/refresh", async (_req: Request, res: Response) => {
  const result = await runPythonModule("database.external_resource_stats", [], {
    processIdTag: EXT_RES_STATS_TAG,
  });
  // The runner prints "done — N date(s) rebuilt" (0 = already current).
  const rebuilt = /(\d+) date\(s\) rebuilt/.exec(result.stdout);
  res.json({
    success: result.success,
    alreadyRunning: result.already_running === true,
    exitCode: result.exitCode,
    rebuiltDates: rebuilt ? Number(rebuilt[1]) : null,
    stderrTail: result.stderr.slice(-1000),
  });
});

/** GET /api/monitor/ext-resources/refresh/status — running-state of the
 *  stats refresh tag, polled on mount so a page refresh mid-run puts the
 *  button straight back into its spinning state. */
router.get("/ext-resources/refresh/status", (_req: Request, res: Response) => {
  res.json({
    running: getPythonProcessStatus([EXT_RES_STATS_TAG])[EXT_RES_STATS_TAG] ?? false,
  });
});

/** GET /api/monitor/ext-resources/configs — the whole per-(service,
 *  request_type) pacing / anti-bot config inventory, read from the
 *  static JSON tree (database/sql/monitoring/static/
 *  external_request_config/ — the config table's replacement; the
 *  shared static-config service owns the file access). One entry per
 *  request type per service (a service can issue several request kinds
 *  with different cadences), so the table is tiny and fetched whole
 *  once. `defaultRows` carries the default.json fallback (60s cooldown)
 *  for services without their own file. */
router.get("/ext-resources/configs", (_req: Request, res: Response) => {
  try {
    const toEntry = (r: ExtRequestConfigRow): ExtRequestConfigEntry => ({
      service: r.service,
      requestType: r.request_type,
      resourceType: decodeLabel("ext_resource", r.resource_type),
      sleepSec: r.sleep_sec,
      sleepJitter: r.sleep_jitter,
      pollIntervalSec: r.poll_interval_sec,
      maxRetries: r.max_retries,
      retryBackoffSec: r.retry_backoff_sec,
      timeoutConnectSec: r.timeout_connect_sec,
      timeoutReadSec: r.timeout_read_sec,
      rotateBrowserProfile: r.rotate_browser_profile,
      addRandomParam: r.add_random_param,
      enableHostTracking: r.enable_host_tracking,
      hostBlockCooldownSec: r.host_block_cooldown_sec,
      notes: r.notes,
      sourceRef: r.source_ref,
    });
    const body: ExtRequestConfigsResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      rows: readExternalRequestConfig().map(toEntry),
      defaultRows: readDefaultExtRequestConfig().map(toEntry),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/ext-resources/configs] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** Live last_date re-derivation per served type. The audit table stores
 *  coverage as of the last build, so its last_date goes stale the moment
 *  new source data lands; each CTE below re-checks the latest data per
 *  security code from the SAME stats tables the writers count:
 *    index → stats.index_identity (plain identity MAX — the index writer's
 *            coverage source),
 *    etf   → stats.etf_identity   (plain identity MAX — the ETF build's
 *            fetch_per_code_day_stats source),
 *    stock → stats.stock_basic_stats WHERE close IS NOT NULL — NOT plain
 *            stock_identity: the streamer writes an identity row per
 *            listed stock per trading day even pre-open / suspended (no
 *            quote), which would overstate freshness (~20% of codes).
 *  Only the SERVED types' CTEs are built (the stock one scans the full
 *  basic_stats table), so a filtered fetch never pays for the others. */
const AUDIT_TYPES = ["index", "etf", "stock"] as const;
type AuditType = (typeof AUDIT_TYPES)[number];

const AUDIT_LIVE_LAST_DATE: Record<
  AuditType,
  { cte: string; cteName: string; typeCode: number }
> = {
  index: {
    cte: `li AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.index_identity GROUP BY code)`,
    cteName: "li",
    typeCode: LABEL_CODE.sec_type.index,
  },
  etf: {
    cte: `le AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.etf_identity GROUP BY code)`,
    cteName: "le",
    typeCode: LABEL_CODE.sec_type.etf,
  },
  stock: {
    cte: `ls AS (SELECT code, MAX(date)::text AS last_date
                   FROM stats.stock_basic_stats
                  WHERE close IS NOT NULL GROUP BY code)`,
    cteName: "ls",
    typeCode: LABEL_CODE.sec_type.stock,
  },
};

/** GET /api/monitor/src-data-audit?sec_type=index|etf|stock|all&q=&limit=
 *  — the per-security source-data coverage audit
 *  (monitor.src_data_audit: n_days / first_date per (sec_type, code),
 *  written by the classification + ETF builds).
 *  last_date is re-derived per code at query time from the stats source
 *  tables (see AUDIT_LIVE_LAST_DATE), so every refresh reflects the data
 *  that actually arrived; codes with no live rows (JSON-only indices)
 *  fall back to the stored audit value.
 *  sec_type filters the SMALLINT label-code column (domain 'sec_type' —
 *  the param is encoded via the label-code contract, rows decode at this
 *  boundary); q narrows by code substring or display name (name JOINed in
 *  from stats.sec_classification — one row per code, primary-parent row
 *  preferred). Ordered sec_type, code for a stable listing; per-type row
 *  counts (decoded keys) ride along for the header. */
interface SrcDataAuditRow {
  sec_type: number;
  code: string;
  exchange: string | null;
  board: number | null;
  n_days: number;
  first_date: string | null;
  last_date: string | null;
  updated_at: Date;
  name: string | null;
}

export interface SrcDataAuditEntry {
  secType: string;
  code: string;
  name: string | null;
  exchange: string | null;
  /** Decoded board label (board label-code domain): MAIN | STAR | GEM |
   *  BSE — stock rows only, null for index/etf. */
  board: string | null;
  nDays: number;
  firstDate: string | null;
  lastDate: string | null;
  updatedAt: string;
}

export interface SrcDataAuditResponse {
  success: true;
  generatedAt: string;
  /** Total rows per sec_type (decoded label keys, unfiltered — the
   *  header's counts). */
  counts: Record<string, number>;
  rows: SrcDataAuditEntry[];
}

router.get("/src-data-audit", async (req: Request, res: Response) => {
  const secTypeRaw = typeof req.query.sec_type === "string" ? req.query.sec_type.trim() : "";
  const q = typeof req.query.q === "string" ? req.query.q.trim() : "";
  // Cap sits above the full universe (~20k rows) so the UI can fetch one
  // sec_type COMPLETE — a "check" table that silently truncates is useless.
  const limit = Math.min(Math.max(Number(req.query.limit) || 20000, 1), 20000);
  // "all" (or omitted) → no sec_type filter; anything else must decode to
  // a 'sec_type' label code — a bad value is a 400, not a silent "all".
  const secTypeCode =
    secTypeRaw === "all" || secTypeRaw === ""
      ? null
      : encodeLabelParam("sec_type", secTypeRaw);
  if (secTypeRaw !== "all" && secTypeRaw !== "" && secTypeCode === null) {
    res.status(400).json({
      success: false,
      error: "query param 'sec_type' must be index | etf | stock | all",
    });
    return;
  }
  try {
    // queryRows routes through the replica-lag probe + primary failover —
    // a rebuilding / replaying replica must 500 this page.
    const countsRows = await queryRows<{ sec_type: number; n: string }>(
      `SELECT sec_type, count(*) AS n FROM monitor.src_data_audit GROUP BY sec_type`,
    );
    const counts: Record<string, number> = {};
    for (const r of countsRows) {
      const label = decodeLabel("sec_type", r.sec_type);
      if (label != null) counts[label] = Number(r.n);
    }
    // Live last_date CTEs for the served types only ($5.. guard each join
    // to its own sec_type — bare 6-digit codes exist in BOTH the index and
    // ETF identity tables, so an unguarded join could cross-match).
    const servedTypes: AuditType[] =
      secTypeRaw === "all" || secTypeRaw === ""
        ? [...AUDIT_TYPES]
        : [secTypeRaw as AuditType];
    const liveCtes = servedTypes
      .map((t) => AUDIT_LIVE_LAST_DATE[t].cte)
      .join(", ");
    const liveJoins = servedTypes
      .map((t, i) => {
        const live = AUDIT_LIVE_LAST_DATE[t];
        return `LEFT JOIN ${live.cteName}
                 ON ${live.cteName}.code = a.code
                AND ${live.cteName}.last_date IS NOT NULL
                AND a.sec_type = $${5 + i}`;
      })
      .join("\n       ");
    const rows = await queryRows<SrcDataAuditRow>(
      `WITH ${liveCtes}
       SELECT a.sec_type, a.code, a.exchange, a.board, a.n_days,
              a.first_date::text AS first_date,
              COALESCE(${servedTypes
                .map((t) => `${AUDIT_LIVE_LAST_DATE[t].cteName}.last_date`)
                .join(", ")}, a.last_date::text) AS last_date,
              a.updated_at, sc.name
       FROM monitor.src_data_audit a
       LEFT JOIN LATERAL (
            SELECT s.name
              FROM stats.sec_classification s
             WHERE s.code = a.code AND s.type = $2::text
             ORDER BY s.parent_index_is_primary DESC
             LIMIT 1
       ) sc ON TRUE
       ${liveJoins}
       WHERE ($1::smallint IS NULL OR a.sec_type = $1)
         AND ($3::text = '' OR a.code ILIKE '%' || $3 || '%'
                         OR sc.name ILIKE '%' || $3 || '%')
       ORDER BY a.sec_type, a.code
       LIMIT $4`,
      [
        secTypeCode,
        secTypeRaw === "all" || secTypeRaw === "" ? null : secTypeRaw,
        q,
        limit,
        ...servedTypes.map((t) => AUDIT_LIVE_LAST_DATE[t].typeCode),
      ],
    );
    const body: SrcDataAuditResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      counts,
      rows: rows.map((r) => ({
        secType: decodeLabel("sec_type", r.sec_type) ?? String(r.sec_type),
        code: r.code,
        name: r.name,
        exchange: r.exchange,
        board: decodeLabel("board", r.board),
        nDays: r.n_days,
        firstDate: r.first_date,
        lastDate: r.last_date,
        updatedAt: r.updated_at.toISOString(),
      })),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/src-data-audit] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

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
 *  the five downloads.stream.* pollers, sleep 1 apart), then a `sleep
 *  infinity` keeper parks the session. The watchdog's first pass heals
 *  live_parallel from pid liveness (a dead poller cannot release its own
 *  slot) and restarts db_health, so this recovers the full all-dead
 *  state; every spawn is max_parallel-guarded, so starting over a healthy
 *  stream is a harmless no-op (duplicates self-exit).
 *
 *  The keeper runs through the shared py-runner spawn — a hidden wsl.exe
 *  child of THIS API server (windowsHide, no cmd window; the mechanism
 *  every other API-triggered python run uses). A WSL session's nohup'd
 *  children die with its wsl.exe client, so the client must stay
 *  connected: the API server holds it for the keeper's whole life, which
 *  also means an API server exit takes the stream down — the button is
 *  the recovery (deduped by process-id-tag while the keeper lives). The
 *  services' real liveness is the heartbeat table (first beats within
 *  ~60s). Keeper output lands in logs/<date>/ui_stream_always.log. */
const STREAM_ALWAYS_TAG = "monitor:stream-always";

/** The keeper command: date's log dir, append everything to the starter
 *  log, idempotent starters, then park (pgrep name stream-always-keeper
 *  kills just the keeper, not the nohup'd services). */
const STREAM_ALWAYS_KEEPER_CMD = [
  "mkdir -p logs/$(date +%F)",
  "exec >> logs/$(date +%F)/ui_stream_always.log 2>&1",
  "bash scripts/stream_watchdog.sh",
  "bash scripts/stream_always.sh",
  "echo [ui] stream-always keeper session up $(date +%T)",
  "exec -a stream-always-keeper sleep infinity",
].join(" && ");

router.post("/stream-always", async (_req: Request, res: Response) => {
  if (isPythonProcessRunning(STREAM_ALWAYS_TAG)) {
    res.json({
      success: true,
      started: false,
      alreadyRunning: true,
      message:
        "stream-always keeper session already up (spawned by this API server) — deduped, no new session",
    });
    return;
  }
  // Fire-and-forget: the POST resolves as soon as the session is handed
  // to WSL. The promise resolves only when the keeper session DIES (API
  // server exit kills the client, WSL tears the session + services down)
  // — log it loudly so a dead stream is explainable from the API log.
  void runWslScript(STREAM_ALWAYS_KEEPER_CMD, "stream-always keeper", {
    processIdTag: STREAM_ALWAYS_TAG,
  }).then(() => {
    console.error(
      "[monitor/stream-always] keeper session exited — the always-on stream died with it; press Start stream always again",
    );
  });
  res.json({
    success: true,
    started: true,
    message:
      "keeper session spawned (hidden) — watchdog starter then the five pollers staggered, first beats within ~60s",
  });
});

/** GET /api/monitor/stream */
router.get("/stream", async (_req: Request, res: Response) => {
  try {
    const pool = getReadPool();
    const beats = await pool.query<BeatRow>(
      `SELECT h.service, h.pid, h.started_at, h.beat_at,
              EXTRACT(EPOCH FROM (now() - h.beat_at))::int AS beat_age_s,
              h.scope, h.sub_scope, h.stop_reason, h.is_normal_exit,
              h.stopped_at, h.live_parallel
       FROM monitor.service_heartbeat h
       ORDER BY h.service`,
    );
    // The watchdog restart trail, merged into service_audit: the dead
    // life's row closed with a 'watchdog restart: …' exit_reason.
    const restarts = await pool.query<RestartRow>(
      `SELECT service, ended_at, pid, exit_reason, scope, sub_scope
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
       ORDER BY ended_at DESC
       LIMIT 20`,
    );
    const counts = await pool.query<{ service: string; restarts_24h: string }>(
      `SELECT service, count(*) AS restarts_24h
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
         AND ended_at > now() - interval '24 hours'
       GROUP BY service`,
    );
    const restarts24h = new Map(
      counts.rows.map((r) => [r.service, Number(r.restarts_24h)]),
    );
    const byService = new Map(beats.rows.map((r) => [r.service, r]));

    const statuses = SERVICE_CATALOG.map((c) =>
      toStatus(c, byService.get(c.service), restarts24h),
    );

    // Batch pipelines: heartbeat rows outside the always-on catalog —
    // their LAST-RUN state (run history is /stream/runs on demand).
    const catalog = new Set(SERVICE_CATALOG.map((c) => c.service));
    const processes: ProcessStatus[] = beats.rows
      .filter((r) => !catalog.has(r.service))
      .map((r): ProcessStatus => {
        const status: ProcessStatus["status"] =
          r.stop_reason != null
            ? "stopped"
            : r.beat_age_s <= STALE_AFTER_S
              ? "running"
              : "stale";
        // Supposed-run cadence from the service_config/ static tree (the
        // default.json fallback supplies on_demand for unlisted services).
        const cfg = serviceConfigFor(r.service);
        return {
          service: r.service,
          scope: r.scope || "unknown",
          subScope: r.sub_scope,
          pid: r.pid,
          startedAt: r.started_at.toISOString(),
          beatAt: r.beat_at.toISOString(),
          beatAgeS: r.beat_age_s,
          status,
          stopReason: r.stop_reason,
          isNormalExit: r.is_normal_exit,
          stoppedAt: r.stopped_at ? r.stopped_at.toISOString() : null,
          liveParallel: r.live_parallel,
          maxParallel: maxParallelFor(r.service), // static JSON config
          freq: cfg.freq,
          freqPeriodMs: freqPeriodMsFor(cfg.freq),
          schedule: cfg.schedule,
          notes: cfg.notes,
          sourceRef: cfg.source_ref,
        };
      });
    // Registered services that NEVER ran: config-tree entries without any
    // heartbeat row (and outside the always-on catalog) — "never" rows so
    // the full-history catalog covers the whole registration, not just
    // what has beaten.
    const beatServices = new Set(beats.rows.map((r) => r.service));
    for (const c of readServiceConfig()) {
      if (c.service === "default" || catalog.has(c.service) || beatServices.has(c.service)) {
        continue;
      }
      processes.push({
        service: c.service,
        // No registration → no recorded scope; the dotted name's head is
        // the natural grouping the scope filter already uses.
        scope: c.service.split(".")[0] ?? "unknown",
        subScope: null,
        pid: null,
        startedAt: null,
        beatAt: null,
        beatAgeS: null,
        status: "never",
        stopReason: null,
        isNormalExit: null,
        stoppedAt: null,
        liveParallel: 0,
        maxParallel: c.max_parallel,
        freq: c.freq,
        freqPeriodMs: freqPeriodMsFor(c.freq),
        schedule: c.schedule,
        notes: c.notes,
        sourceRef: c.source_ref,
      });
    }
    processes.sort((a, b) => {
      if (a.status !== b.status) {
        const rank = (s: ProcessStatus["status"]) =>
          s === "running" ? 0 : s === "never" ? 2 : 1;
        return rank(a.status) - rank(b.status);
      }
      return a.service.localeCompare(b.service);
    });

    const body: MonitorStreamResponse = {
      success: true,
      generatedAt: new Date().toISOString(),
      staleAfterS: STALE_AFTER_S,
      watchdog:
        statuses.find((s) => s.service === WATCHDOG_SERVICE) ?? null,
      services: statuses.filter((s) => s.service !== WATCHDOG_SERVICE),
      processes,
      recentRestarts: restarts.rows.map((r) => ({
        service: r.service,
        scope: r.scope,
        subScope: r.sub_scope,
        restartedAt: r.ended_at.toISOString(),
        pid: r.pid,
        reason: r.exit_reason,
      })),
    };
    res.json(body);
  } catch (err) {
    console.error("[monitor/stream] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
