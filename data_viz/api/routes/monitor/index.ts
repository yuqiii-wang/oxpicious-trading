// The route-catalog doc comment below is extracted verbatim from
// routes/monitor.ts — the routes themselves live in this directory's
// subfiles; this file composes them in the original section order.
/**
 * Monitoring API routes — service process liveness + run audit + the
 * external-resource request audit aggregates.
 *
 *   GET /api/monitor/stream
 *     → heartbeat state of the resident processes (the RESIDENT service
 *       catalog = the always-on services the stream watchdog supervises:
 *       the five downloads.stream.* pollers, the db_health sampler, the
 *       llm_agents daemons, the watchdog itself — scope + sub_scope per
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
 *   GET /api/monitor/stream/runs/daily?service=...&days=90
 *     → daily run counts of ONE service over the trailing window
 *       (Shanghai-local days, gap-filled with 0) + per-day abnormal
 *       counts: the per-service run-trend curve every monitor row
 *       expansion mounts (the shared ServiceRunTrend component).
 *
 *   GET /api/monitor/stream/runs/day?date=YYYY-MM-DD[&limit=2000]
 *     → every run of ONE Shanghai-local day across ALL services (the
 *       day drill-down under the curve; same run rows as /stream/runs).
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
 *       quarterly = 92d / semiannually = 183d / once = any success;
 *       always / occasionally /
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
 *     → start the Orchestration config tab's tier run on the monitor
 *       area (server_cluster/monitor POST /orchestration/run — the
 *       tier executor; the maintainer's nightly_pipeline chain drives
 *       these same runs at 16:00 China time on trading days): steps run
 *       in numeric key order — download tiers in diff-src-parallel
 *       waves (consecutive steps with distinct src_host run
 *       concurrently, the whole wave before the next) — through its job
 *       registry, each heartbeating onto Pipeline processes as it
 *       starts. GET /orchestration/runs/:tag polls step statuses;
 *       dryRun=true echoes the plan without executing (smoke only).
 *       The maintainer's chain posts with arrange=true: the monitor
 *       area first upserts the tier's arrangement
 *       (monitor.orchestration_task_state — each step's data pre-check
 *       where one is declared, else its freq-window verdict) and skips
 *       not-due steps (state skipped_not_due) instead of spawning them
 *       to no-op.
 *
 *   GET /api/monitor/orchestration/board
 *     → the ARRANGEMENT BOARD (the Orchestration tab): every
 *       orchestration-tier service's arrangement row
 *       (monitor.orchestration_task_state — the verdict the monitor
 *       area's OrchestrationArranger wrote: the Shanghai-calendar freq
 *       rule, or the data pre-check for a service that declares one;
 *       state and basis DECODED via the label_dim join) classified into
 *       three sections — `running` (heartbeat-live, pid/started/beat
 *       joined), `queue` (arranged due, not yet successfully run;
 *       priority-desc, with the pre-check reading + session lists and
 *       the non-daily missing/attempts/next-check tooltip fields),
 *       `noNeed` (the rest, most-recent first, with the reason: data
 *       present | csv staged | awaiting source csv (the pre-check) |
 *       within freq window | once — done | ran since arranged | no
 *       window) — each row enriched with its service_config
 *       schedule/sourceRef. An empty (never-arranged) table bootstraps
 *       through one arrangement pass before the read.
 *
 *   POST /api/monitor/orchestration/arrange
 *     → one arrangement pass over every tier file NOW (the page's
 *       Arrange now action — the same verdicts the nightly chain's
 *       per-tier arrange side applies: the data pre-check where
 *       declared, else the freq rule), then the refreshed board.
 *
 *   GET /api/monitor/non-daily
 *     → the non-daily tasks' completeness monitor (the Non-daily tab):
 *       every non-daily orchestration tier's tasks (downloads
 *       monthly/quarterly/semiannually/once, builds monthly/
 *       semiannually/once, strategy once, monitoring occasionally)
 *       merged with their check configs (static non_daily_checks tree),
 *       their current state rows (monitor.non_daily_task_state — the
 *       identity-grounded verdicts: complete | missing | check_failed,
 *       state arrives already DECODED via the label_dim join) and their
 *       service_config cadence (freq + schedule — how the task should
 *       run). missing_count/missing_detail/attempts/next_check_at
 *       expose the weekend backfill's charge (Saturday 05:00 China
 *       time, weekly retry until loaded).
 *
 *   POST /api/monitor/non-daily/evaluate
 *     → one check pass NOW (what the page's Evaluate action fires): the
 *       monitor area evaluates every configured task against the daily
 *       data identities and upserts the verdicts.
 *
 *   POST /api/monitor/non-daily/backfill  { dryRun? }
 *     → the weekend drive on demand (what the maintainer's Saturday
 *       non_daily_backfill job fires): evaluate, re-run each DUE
 *       missing re-run-eligible task (module + tier args + force args
 *       via the run registry), re-check, bump attempts + the weekly
 *       next_check gate. dryRun=true reports the due list only.
 *
 *   GET /api/monitor/llm-host/models
 *     → the local-model host's admin list (server_cluster/llm, 8767):
 *       every configured local model with its GPU lifecycle state
 *       under `local` (loaded = weights on the GPU, offloaded = in RAM
 *       off the GPU, unloaded = next use cold-loads from disk).
 *
 *   POST /api/monitor/llm-host/models/offload-all
 *     → the manual save-memory action: every LOADED model's weights
 *       move GPU → CPU right now (already-offloaded models skipped, a
 *       failing model skipped so the rest still offload; the next
 *       request rehydrates transparently). The operator's one-call
 *       VRAM free before the GPU-hungry pipelines run.
 *
 *   POST /api/monitor/llm-host/models/:id/:action
 *     → one model's admin lifecycle action (load | offload | unload).
 *
 *   POST /api/monitor/health-check
 *     → the monitor area's GET /health-check (database.monitoring's
 *       probes — replica sync, replay backlog, slot WAL retention,
 *       pg_wal dir size vs max_wal_size, always-on liveness) and return
 *       its [ok] / [ALERT] lines + the health verdict. The UI's refresh
 *       button on the Service Monitor page fires this.
 *
 *   GET /api/monitor/fleet
 *     → the monitor area's aggregated GET /residents (ONE call; the
 *       monitor's FleetOps fans out ONE GET per area server server-side
 *       — /processes?detail=1) with each resident row's unified detail
 *       folded: supervisor row + heartbeat evidence + the DERIVED
 *       progress (the missing-set oracle — the pollers' per-asset
 *       completeness, computed from the data tables at the owning
 *       servers) + the child-reported progress. The 2026-11-21 control
 *       unification plan's query rule: the UI queries the fleet VIA THE
 *       SERVER, never per-process. The Service Monitor page merges the
 *       detail into its resident rows by service name.
 *
 *   POST /api/monitor/fleet/command  { service, command, payload? }
 *     → the ONE downward-control face (Phase 4's command mailbox): a
 *       cooperative signal (flush_now | pause | resume) enqueued into
 *       the target child's mailbox at its OWNING area server (the
 *       py-runner's cluster routing). The child's heartbeat thread
 *       claims + consumes it atomically within ~5s and acts at its next
 *       safe checkpoint; the area server's class gate refuses a PAUSE
 *       for a not-pausable / gpu-class service (409 with the reason).
 *       The Service Monitor page's per-row ⋮ menu fires this.
 *
 *   POST /api/monitor/stream-always
 *     → bring the always-on fleet up via the monitor area's POST
 *       /residents/ensure-up: every desired-running-but-down resident
 *       gets an explicit start, a stale stream watchdog is spawned.
 *       Idempotent over a healthy fleet. Recurring supervision stays the
 *       stream watchdog's (every ~60s) plus the maintainer area's daily
 *       07:00 China-time pass (migrated from the monitor area 2026-11;
 *       it fires this same endpoint). The UI's "Start stream always"
 *       button fires this.
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
 *   GET /api/monitor/db/tables?hours=24&order=workload|recent&limit=500
 *                                     [&source=live|archive]
 *     → the DB tab's per-table workload panel: MAIN TABLES ONLY — the
 *       LATEST audit row per table over a trailing window
 *       (monitor.db_table_audit, written ACTIVITY-SPARSE by the
 *       database.db_table_audit sampler — a row only when a table's
 *       pg_stat_user_tables counters moved or its flag changed, so
 *       absence = idle). hours=0 = the full retained history (no lower
 *       bound — the sampler's --retain-days prunes the table; surfaces
 *       tables idle in recent windows whose last active pass is older).
 *       source=archive swaps the store to the computed daily rollup
 *       (monitor.db_table_audit_archive — one record per GMT day /
 *       table / instance, kept indefinitely, database/
 *       db_table_audit_archive.py) collapsed to a LIFETIME aggregate per
 *       table: day sums of ins/upd/del added up, peak write/read rates
 *       the worst day's, write_flag the worst-ever verdict, live/dead tup
 *       and size the latest archived day's end-of-day snapshots, stamps
 *       the most recent active day (no window — hours echoes 0). Partition
 *       children never appear here: each partition FAMILY is folded into
 *       its parent row — write/read rates and delta counts summed across
 *       the partitions, flag the max family severity, stamps the latest
 *       of the family — and the partitions load lazily per family via
 *       /db/partitions. Write side from the primary (write_rate +
 *       write_flag, the fixed-threshold amber/red verdict) and read side
 *       from the replica (where the API reads), FULL OUTER JOINed. order
 *       (whitelisted) = workload (combined write+read rate, the default)
 *       | recent (the later last-active stamp), applied before the limit.
 *       write_flag arrives DECODED (db_write_flag; null = not evaluated).
 *
 *   GET /api/monitor/db/partitions?schema=...&table=...&hours=24
 *                                  [&source=live|archive]
 *     → the live partition children of ONE partitioned parent (the lazy
 *       expansion behind a workload-panel family row): every partition
 *       present with its LATEST audit row in the window when active —
 *       NULL metrics = idle (activity-sparse, like the panel).
 *       source=archive swaps to the rollup table's LIFETIME aggregate per
 *       partition (no window).
 *
 *   GET /api/monitor/db/trend?schema=...&table=...&hours=6
 *                             [&source=live|archive]
 *     → ONE table's write/read workload trend on the primary over a
 *       trailing window — the drill-down chart behind a row click
 *       (activity-sparse: idle gaps have no points). source=archive swaps
 *       to the rollup table's per-GMT-day series (one point per archived
 *       day over the FULL archive history; each point carries the GMT
 *       operational day as `date`). FAMILY-AWARE: a partitioned parent's
 *       passes aggregate its whole family (its own write-through rows are
 *       idle zeros) and the response flags family=true; plain tables and
 *       partitions return their own rows.
 *
 *   GET /api/monitor/db/alerts?limit=50
 *     → the write-heavy alert trail (monitor.db_table_alert): every
 *       write_flag STATE TRANSITION (raise / downgrade / clear) with
 *       severity + the tripping rate vs the threshold in effect.
 *       Severity arrives DECODED (db_write_flag; 0 ok = a clear).
 *
 *   GET /api/monitor/db/sql?order=total_ms|calls|recent|avg_ms&limit=100&q=
 *     → the SQL statement audit (monitor.sql_audit): one row per DISTINCT
 *       statement executed on the cluster (pg_stat_statements sampled on
 *       BOTH instances by database/sql_audit.py — every client: pipelines,
 *       this API, the MCP server, psql), keyed by sha256 of the
 *       normalized statement text (literals -> $n) with delta-accumulated
 *       call / total / avg / max exec time / row totals. order maps
 *       through a route whitelist to the ORDER BY; q narrows to a text
 *       substring or a sql_hash prefix.
 *
 *   GET /api/monitor/db/instances?hours=24
 *     → the DB tab's instance vitals panel: the LATEST monitor.db_health
 *       row per instance (the 60s sampler — reachability, latency,
 *       connections, cache hit, WAL shipping state + replay backlog,
 *       pg_wal size, container CPU/mem) plus the trailing connections /
 *       cache-hit / backlog trend. instance + replication_state arrive
 *       DECODED (db_instance / db_replication_state).
 *
 *   GET /api/monitor/cautions?state=open|resolved|all&source=&severity=&limit=
 *     → the cross-service caution trail (monitor.caution_log — the
 *       Cautions tab): one OPEN row per issue per service (deduped on
 *       (service, dedupe_key) over open rows — a recurrence bumps
 *       occurrence_count / last_seen_at), covering running-service
 *       errors (abnormal pipeline exits via service_heartbeat,
 *       orchestration step failures, maintenance job crashes, the API's
 *       own unhandled errors + py-runner never-started runs), AI-found
 *       suspicions (output-validation complaints that survive their
 *       repair round, [CRITICAL]-flagged findings, below-floor /
 *       unavailable jev reads, deep-agents daemon failures) and manual
 *       pending bugs (python -m _common.caution_log). source /
 *       severity_level arrive DECODED (caution_source /
 *       caution_severity); ordering is open-first, worst severity
 *       first, most-recent-hit first.
 *
 *   POST /api/monitor/cautions { service, title, severity, source?,
 *       dedupeKey?, detail?, logContext? }
 *     → register ONE caution from any writer that cannot go DB-direct
 *       (ops scripts, non-Python services, sandboxed code — Python
 *       services keep using `_common.caution_log` DB-direct). Same
 *       upsert + dedupe contract as every writer: (service, dedupeKey)
 *       over OPEN rows, a recurrence bumps occurrence_count /
 *       last_seen_at. severity is required (info | warning | error |
 *       critical), source defaults to service; an unknown label name is
 *       a 400 (the direct caller gets the rejection the internal
 *       never-throw writers swallow). logContext { file?, keyword?,
 *       timeFrom?, timeTo? } rides into detail.log_context — how to
 *       locate the error in the logs (the Cautions tab's locate-in-logs
 *       line; missing time bounds are stamped with the record time).
 *       Python-side writers auto-carry the derived context (the day's
 *       logs/<date>/<service>.log + record time) — detail.log_context
 *       from any writer wins over the derived default.
 *
 *   POST /api/monitor/cautions/resolve { id }
 *     → resolve ONE open caution row (is_resolved = true +
 *       resolved_timestamp = now(), the Cautions tab's Resolve action).
 *       A resolved row is history — never re-opened; a recurrence opens
 *       a fresh row.
 *
 * Reads monitor.service_heartbeat / monitor.service_audit /
 * monitor.external_resource_stats / monitor.external_resource_audit /
 * monitor.src_data_audit / monitor.caution_log on
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
import orchestrationBoardRouter from "./orchestration-board.js";
import nonDailyRouter from "./non-daily.js";
import extResourcesRouter from "./ext-resources.js";
import srcDataAuditRouter from "./src-data-audit.js";
import streamAlwaysRouter from "./stream-always.js";
import streamRouter from "./stream.js";
import fleetRouter from "./fleet.js";
import aiAuditRouter from "./ai-audit.js";
import dbAuditRouter from "./db-audit.js";
import cautionsRouter from "./cautions.js";
import llmHostRouter from "./llm-host.js";

const router = Router();

router.use(runsRouter);
router.use(runGuardRouter);
router.use(orchestrationRouter);
router.use(orchestrationBoardRouter);
router.use(nonDailyRouter);
router.use(extResourcesRouter);
router.use(srcDataAuditRouter);
router.use(streamAlwaysRouter);
router.use(streamRouter);
router.use(fleetRouter);
router.use(aiAuditRouter);
router.use(dbAuditRouter);
router.use(cautionsRouter);
router.use(llmHostRouter);

// Re-exported for server.ts, which imports the boot auto-start helpers
// from routes/monitor/index.js.
export { ensureStreamsAtBoot, checkStreamProcessesAlive } from "./stream-always.js";

export default router;
