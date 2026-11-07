// The route-catalog doc comment below is extracted verbatim from
// routes/monitor.ts — the routes themselves live in this directory's
// subfiles; this file composes them in the original section order.
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

import { Router } from "express";
import runsRouter from "./runs.js";
import runGuardRouter from "./run-guard.js";
import orchestrationRouter from "./orchestration.js";
import extResourcesRouter from "./ext-resources.js";
import srcDataAuditRouter from "./src-data-audit.js";
import streamAlwaysRouter from "./stream-always.js";
import streamRouter from "./stream.js";
import aiAuditRouter from "./ai-audit.js";

const router = Router();

router.use(runsRouter);
router.use(runGuardRouter);
router.use(orchestrationRouter);
router.use(extResourcesRouter);
router.use(srcDataAuditRouter);
router.use(streamAlwaysRouter);
router.use(streamRouter);
router.use(aiAuditRouter);

// Re-exported for server.ts, which imports the boot auto-start helpers
// from routes/monitor/index.js.
export { ensureStreamsAtBoot, startStreamAlwaysKeeper, checkStreamProcessesAlive } from "./stream-always.js";

export default router;
