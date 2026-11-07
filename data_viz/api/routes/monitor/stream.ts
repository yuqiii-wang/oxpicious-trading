/**
 * GET /stream — always-on stream heartbeat/watchdog status + the batch
 * pipeline process rows (last-run state + never-run registrations).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getReadPool,
} from "../../services/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import {
  maxParallelFor,
  readServiceConfig,
  serviceConfigFor,
} from "../../services/static-config.service.js";
import { FREQ_PERIOD_MS, SERVICE_CATALOG, STALE_AFTER_S, WATCHDOG_SERVICE } from "./_shared.js";

const router = Router();

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

/** The finite window of ``freq`` (null = no checkable period: the
 *  no-window freqs, "once" whose any-success rule never lapses, and
 *  unknown values). JSON-serializable form of FREQ_PERIOD_MS — the
 *  pipeline-process rows carry it so the UI can amber an overdue
 *  Last beat against the supposed cadence. */
function freqPeriodMsFor(freq: string): number | null {
  const period = FREQ_PERIOD_MS[freq] ?? null;
  return period != null && Number.isFinite(period) ? period : null;
}

/** GET /api/monitor/stream */
router.get("/stream", async (_req: Request, res: Response) => {
  try {
    const pool = getReadPool();
    const beatsView = renderSqlView("data_viz/api/routes/monitor/stream_heartbeats");
    const restartsView = renderSqlView(
      "data_viz/api/routes/monitor/stream_watchdog_restarts",
    );
    const counts24hView = renderSqlView(
      "data_viz/api/routes/monitor/stream_restarts_24h",
    );
    const beats = await pool.query<BeatRow>(beatsView.sql, beatsView.params);
    // The watchdog restart trail, merged into service_audit: the dead
    // life's row closed with a 'watchdog restart: …' exit_reason.
    const restarts = await pool.query<RestartRow>(
      restartsView.sql,
      restartsView.params,
    );
    const counts = await pool.query<{ service: string; restarts_24h: string }>(
      counts24hView.sql,
      counts24hView.params,
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
