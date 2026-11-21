/**
 * GET /orchestration/board + POST /orchestration/arrange — the
 * ARRANGEMENT BOARD: what the maintainer arranged to run vs skip
 * (monitor.orchestration_task_state, written by the monitor area's
 * OrchestrationArranger off each service's service_config freq), shown
 * as the Monitor UI's Orchestration page's three sections — running
 * (heartbeat-live), the to-run queue, and the recently-no-need rows —
 * each row enriched with its service_config cadence (schedule / config
 * source) and its non-daily completeness state (the queue's
 * missing / attempts tooltip).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import { getReadPool } from "../../services/infra/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import {
  monitorArrangementRefresh,
  type MonitorArrangementRefresh,
} from "../../services/infra/monitor-client.service.js";
import { readServiceConfig } from "../../services/infra/static-config.service.js";

const router = Router();

interface BoardRow {
  service: string;
  tier: string;
  step: string;
  freq: string;
  priority: number;
  state: number;
  state_label: string | null;
  last_success_at: Date | null;
  due_at: Date | null;
  arranged_at: Date | null;
  updated_at: Date;
  pid: number | null;
  hb_started_at: Date | null;
  beat_at: Date | null;
  beat_age_s: number | null;
  nd_state_code: number | null;
  nd_state_label: string | null;
  missing_count: number | null;
  attempts: number | null;
  next_check_at: Date | null;
  success_since_arranged: boolean;
}

/** One board task — the wire shape all three sections share (the UI's
 *  rows expand into the per-service run-trend off `service`). */
export interface OrchestrationBoardTask {
  service: string;
  tier: string;
  step: string;
  freq: string;
  priority: number;
  planState: "unknown" | "due" | "not_due";
  stateCode: number;
  lastSuccessAt: string | null;
  dueAt: string | null;
  arrangedAt: string | null;
  successSinceArranged: boolean;
  /** Live-heartbeat join — present only in the running section. */
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  /** Non-daily completeness join (nulls when the task has no row). */
  ndState: string | null;
  ndMissingCount: number | null;
  ndAttempts: number | null;
  ndNextCheckAt: string | null;
  /** service_config cadence enrichment. */
  schedule: string | null;
  sourceRef: string | null;
  /** Why the task sits in the no-need section (null elsewhere). */
  reason: string | null;
}

function isoOrNull(d: Date | null): string | null {
  return d != null ? d.toISOString() : null;
}

/** Read the joined board rows and classify the three sections. A
 *  never-written table bootstraps itself through one arrangement pass
 *  (fresh deploys — the nightly chain arranges nightly thereafter). */
async function loadBoard(): Promise<{
  running: OrchestrationBoardTask[];
  queue: OrchestrationBoardTask[];
  noNeed: OrchestrationBoardTask[];
}> {
  const readBoardRows = async (): Promise<BoardRow[]> => {
    const pool = getReadPool();
    const { sql, params } = renderSqlView(
      "data_viz/api/routes/monitor/orchestration_board",
    );
    const rows = await pool.query<BoardRow>(sql, params);
    return rows.rows;
  };
  let rows = await readBoardRows();
  if (rows.length === 0) {
    try {
      await monitorArrangementRefresh();
      rows = await readBoardRows();
    } catch {
      // An unreachable monitor area leaves the empty board — surfaced
      // by the arrange route, not silently retried on every poll.
    }
  }
  const configByService = new Map(
    readServiceConfig().map((entry) => [entry.service, entry]),
  );
  const toTask = (r: BoardRow, reason: string | null): OrchestrationBoardTask => {
    const entry = configByService.get(r.service);
    return {
      service: r.service,
      tier: r.tier,
      step: r.step,
      freq: r.freq,
      priority: r.priority,
      planState: (r.state_label as OrchestrationBoardTask["planState"]) ?? "unknown",
      stateCode: r.state,
      lastSuccessAt: isoOrNull(r.last_success_at),
      dueAt: isoOrNull(r.due_at),
      arrangedAt: isoOrNull(r.arranged_at),
      successSinceArranged: r.success_since_arranged,
      pid: r.pid,
      startedAt: isoOrNull(r.hb_started_at),
      beatAt: isoOrNull(r.beat_at),
      beatAgeS: r.beat_age_s,
      ndState: r.nd_state_label,
      ndMissingCount: r.missing_count,
      ndAttempts: r.attempts,
      ndNextCheckAt: isoOrNull(r.next_check_at),
      schedule: entry?.schedule ?? null,
      sourceRef: entry?.source_ref ?? null,
      reason,
    };
  };
  const running: OrchestrationBoardTask[] = [];
  const queue: OrchestrationBoardTask[] = [];
  const noNeed: OrchestrationBoardTask[] = [];
  for (const r of rows) {
    if (r.pid != null) {
      running.push(toTask(r, null));
      continue;
    }
    if (r.state_label === "due" && !r.success_since_arranged) {
      queue.push(toTask(r, null));
      continue;
    }
    // The no-need section — why: a due service that already exited
    // normally since the arrangement (its window restarted), a window
    // that still holds, or a satisfied once.
    const reason =
      r.state_label === "due"
        ? "ran since arranged"
        : r.freq === "once"
          ? "once — done"
          : r.due_at != null
            ? "within freq window"
            : "no window";
    noNeed.push(toTask(r, reason));
  }
  // The no-need section reads most-recent first (the freshest
  // arrangement / success at the top); running + queue keep the view's
  // priority-desc order from the SQL.
  noNeed.sort((a, b) =>
    (b.lastSuccessAt ?? b.arrangedAt ?? "").localeCompare(
      a.lastSuccessAt ?? a.arrangedAt ?? "",
    ),
  );
  return { running, queue, noNeed };
}

/** GET /api/monitor/orchestration/board — the three sections over the
 *  arrangement table: running = heartbeat-live services; queue =
 *  arranged due and not yet successfully run; noNeed = the rest with
 *  the reason. Light join read — polled by the page like the stream. */
router.get("/orchestration/board", wrapAsync(async (_req: Request, res: Response) => {
  try {
    const board = await loadBoard();
    res.json({ success: true, generatedAt: new Date().toISOString(), ...board });
  } catch (err) {
    throw new HttpError(
      500,
      "INTERNAL",
      err instanceof Error ? err.message : String(err),
    );
  }
}));

/** POST /api/monitor/orchestration/arrange — one arrangement pass over
 *  every tier file (the monitor area's Shanghai-calendar freq rule),
 *  then the refreshed board. What the nightly chain's per-tier arrange
 *  side does, on demand. */
router.post("/orchestration/arrange", wrapAsync(async (_req: Request, res: Response) => {
  let arranged: MonitorArrangementRefresh;
  try {
    arranged = await monitorArrangementRefresh();
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
  const board = await loadBoard();
  res.json({
    success: true,
    generatedAt: new Date().toISOString(),
    arranged: {
      tiers: arranged.tiers,
      services: arranged.services,
      due: arranged.due,
      notDue: arranged.not_due,
      notDueServices: arranged.not_due_services,
    },
    ...board,
  });
}));

export default router;
