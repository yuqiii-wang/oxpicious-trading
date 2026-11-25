/**
 * GET /orchestration/board + POST /orchestration/arrange — the
 * ARRANGEMENT BOARD: what the maintainer arranged to run vs skip
 * (monitor.orchestration_task_state, written by the monitor area's
 * OrchestrationArranger off each service's service_config freq — or,
 * for a service that declares one, its data pre-check), shown as the
 * Monitor UI's Orchestration page's three sections — running
 * (heartbeat-live), the to-run queue, and the recently-no-need rows —
 * each row enriched with its service_config cadence (schedule / config
 * source), the data pre-check's reading behind the verdict (basis +
 * the absent / staged sessions), and its non-daily completeness state
 * (the queue's missing / attempts tooltip).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import { getReadPool } from "../../services/infra/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import {
  monitorArrangementRefresh,
  monitorPipelineRunning,
  type MonitorArrangementRefresh,
} from "../../services/infra/monitor-client.service.js";
import { readServiceConfig } from "../../services/infra/static-config.service.js";

const router = Router();

/** WHAT an arrangement verdict rests on: calendar = the freq rule (the
 *  service declares no data pre-check); the rest are the nightly
 *  chain's data pre-check over the latest closed sessions. */
export type OrchestrationBasis =
  | "calendar"
  | "data_present"
  | "db_missing"
  | "csv_present"
  | "csv_missing"
  | "check_failed";

/** The data pre-check's session lists behind a verdict (ISO dates). */
export interface OrchestrationPreCheckDetail {
  kind?: "fetch" | "load" | "derive";
  /** The data table slices that were probed. */
  tables?: string[];
  /** The probed window's first / last expected session. */
  from?: string;
  to?: string;
  /** Expected sessions absent from every probed table slice. */
  missing?: string[];
  /** Absent sessions whose source csv is staged (a final file). */
  csv_present?: string[];
  /** Absent sessions whose source csv is not staged. */
  csv_missing?: string[];
  /** A multi-slice service: the sessions each lacking table slice
   *  misses (which exchange / family is behind). */
  slice_missing?: Record<string, string[]>;
  /** The probe's error (check_failed). */
  error?: string;
}

interface BoardRow {
  service: string;
  tier: string;
  step: string;
  freq: string;
  priority: number;
  state: number;
  state_label: string | null;
  pc_basis_code: number;
  pc_basis_label: string | null;
  pc_missing_count: number;
  pc_missing_detail: OrchestrationPreCheckDetail | string | null;
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
  p95_run_s: number | null;
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
  /** The evidence behind the verdict (see OrchestrationBasis). */
  basis: OrchestrationBasis;
  /** The sessions the pre-check's verdict turns on (to fetch / to
   *  load / absent); 0 under the calendar rule. */
  missingCount: number;
  /** The pre-check's session lists (null under the calendar rule). */
  missingDetail: OrchestrationPreCheckDetail | null;
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
  /** p95 run duration in seconds — the duration 95% of the service's
   *  last 20 completed normal-exit runs finished within (null when the
   *  service has no such run on record). */
  p95RunS: number | null;
  /** service_config cadence enrichment. */
  schedule: string | null;
  sourceRef: string | null;
  /** Why the task sits in its section: the queue — the pre-check reading
   *  that made it due (null when the calendar rule decided); no-need —
   *  what holds it (null in the running section). */
  reason: string | null;
}

function isoOrNull(d: Date | null): string | null {
  return d != null ? d.toISOString() : null;
}

function preCheckDetail(
  raw: OrchestrationPreCheckDetail | string | null,
): OrchestrationPreCheckDetail | null {
  return typeof raw === "string" ? (JSON.parse(raw) as OrchestrationPreCheckDetail) : raw;
}

function sessions(n: number): string {
  return `${n} session${n === 1 ? "" : "s"}`;
}

/** The queue line of a task the data pre-check put there — which
 *  sessions the verdict turns on (null when the calendar rule decided). */
function dueReason(r: BoardRow): string | null {
  switch (r.pc_basis_label) {
    case "db_missing":
      return `db missing ${sessions(r.pc_missing_count)}`;
    case "csv_missing":
      return `to fetch ${sessions(r.pc_missing_count)} — no final csv`;
    case "csv_present":
      return `to load ${sessions(r.pc_missing_count)} — csv staged`;
    case "check_failed":
      return "pre-check failed — runs fail-open";
    default:
      return null;
  }
}

/** The no-need line: what the data pre-check found when it holds the
 *  task (a pre-checked task never sits here on the calendar rule). */
const HELD_BY_PRE_CHECK: Partial<Record<OrchestrationBasis, string>> = {
  data_present: "data present (pre-check)",
  csv_present: "csv staged — its build loads it",
  csv_missing: "awaiting source csv — runs after its download",
};

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
      basis: (r.pc_basis_label as OrchestrationBasis) ?? "calendar",
      missingCount: r.pc_missing_count,
      missingDetail: preCheckDetail(r.pc_missing_detail),
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
      p95RunS: r.p95_run_s,
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
      queue.push(toTask(r, dueReason(r)));
      continue;
    }
    // The no-need section — why: a due service that already exited
    // normally since the arrangement (its window restarted); else what
    // the data pre-check found for a pre-checked service (its data is
    // present, its csv is staged for the build, or it waits for its
    // download); else the calendar rule — a window that still holds, or
    // a satisfied once.
    const preChecked = r.pc_basis_label as OrchestrationBasis | null;
    const reason =
      r.state_label === "due"
        ? "ran since arranged"
        : HELD_BY_PRE_CHECK[preChecked ?? "calendar"] ??
          (r.freq === "once"
            ? "once — done"
            : r.due_at != null
              ? "within freq window"
              : "no window");
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
 *  every tier file (the monitor area's Shanghai-calendar freq rule,
 *  plus each service's data pre-check where one is declared), then the
 *  refreshed board. What the nightly chain's per-tier arrange side
 *  does, on demand.
 *  REFUSED (409 CONFLICT) while a chain pass is executing: the chain
 *  arranges each tier right before running it, and a manual pass
 *  overlapping that instant is turned away by the monitor as "an
 *  arrangement pass is already running" — which the chain reads as a
 *  conflict and aborts on. The board itself keeps refreshing. */
router.post("/orchestration/arrange", wrapAsync(async (_req: Request, res: Response) => {
  if (await monitorPipelineRunning()) {
    throw new HttpError(
      409,
      "CONFLICT",
      "a nightly chain pass is running — Arrange now would collide with " +
        "the chain's own per-tier arrange and abort it. The board keeps " +
        "refreshing on its own; Arrange now is available once the pass ends.",
    );
  }
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
      byBasis: arranged.by_basis ?? {},
      checkFailedServices: arranged.check_failed_services ?? [],
    },
    ...board,
  });
}));

export default router;
