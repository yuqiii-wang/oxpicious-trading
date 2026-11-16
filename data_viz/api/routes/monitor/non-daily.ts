/**
 * Non-daily tasks — the monthly / quarterly / semiannual / once tasks'
 * completeness monitor + weekend-backfill surface: the monitor area's
 * /non-daily face (server_cluster/monitor/non_daily.py — the
 * identity-grounded checks over monitor.non_daily_task_state) merged
 * with each task's service_config cadence (freq + schedule, "how the
 * task should run").
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  monitorNonDailyBackfill,
  monitorNonDailyEvaluate,
  monitorNonDailyInventory,
  type MonitorNonDailyTask,
} from "../../services/infra/monitor-client.service.js";
import { readServiceConfig } from "../../services/infra/static-config.service.js";

const router = Router();

/** One GET /api/monitor/non-daily row: the monitor area's inventory
 *  task (camelized — the API boundary decodes/renames, per the
 *  src-data-audit route's contract) enriched with its service_config
 *  cadence fields. */
export interface NonDailyTaskRow {
  service: string;
  tier: string;
  step: string;
  script: string;
  args: string[];
  check: string | null;
  rerunArgs: string[];
  rerunEligible: boolean;
  state: string;
  stateCode: number;
  missingCount: number;
  missingDetail: Record<string, unknown> | null;
  attempts: number;
  lastCheckAt: string | null;
  lastRerunAt: string | null;
  nextCheckAt: string | null;
  freq: string | null;
  schedule: string | null;
}

/** GET /api/monitor/non-daily — every non-daily tier's tasks (tier
 *  step + check config + current state), each merged with its
 *  service_config freq/schedule. The state comes from the monitor
 *  area's own view of monitor.non_daily_task_state — call
 *  POST /evaluate first (or the page's Evaluate action) when fresh
 *  verdicts are wanted. */
router.get("/non-daily", wrapAsync(async (_req: Request, res: Response) => {
  let tasks: MonitorNonDailyTask[];
  try {
    tasks = (await monitorNonDailyInventory()).tasks;
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
  const configByService = new Map(
    readServiceConfig().map((entry) => [entry.service, entry]),
  );
  const rows: NonDailyTaskRow[] = tasks.map((task) => {
    const entry = configByService.get(task.service);
    return {
      service: task.service,
      tier: task.tier,
      step: task.step,
      script: task.script,
      args: task.args,
      check: task.check,
      rerunArgs: task.rerun_args,
      rerunEligible: task.rerun_eligible,
      state: task.state,
      stateCode: task.state_code,
      missingCount: task.missing_count,
      missingDetail: task.missing_detail,
      attempts: task.attempts,
      lastCheckAt: task.last_check_at,
      lastRerunAt: task.last_rerun_at,
      nextCheckAt: task.next_check_at,
      freq: entry?.freq ?? null,
      schedule: entry?.schedule ?? null,
    };
  });
  res.json({ success: true, generatedAt: new Date().toISOString(), tasks: rows });
}));

/** POST /api/monitor/non-daily/evaluate — one check pass over every
 *  configured task (the verdicts upsert into
 *  monitor.non_daily_task_state; a passing check resets the backfill
 *  attempt counter). */
router.post("/non-daily/evaluate", wrapAsync(async (_req: Request, res: Response) => {
  try {
    const result = await monitorNonDailyEvaluate();
    res.json({ success: true, ...result });
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
}));

/** POST /api/monitor/non-daily/backfill {dryRun?} — the weekend drive
 *  on demand (what the maintainer's Saturday 05:00 job fires): evaluate,
 *  re-run each DUE missing re-run-eligible task with its force args,
 *  re-check, schedule the weekly retry. dryRun=true reports the due
 *  list without submitting anything. */
router.post("/non-daily/backfill", wrapAsync(async (req: Request, res: Response) => {
  const dryRun = req.body?.dryRun === true;
  try {
    const result = await monitorNonDailyBackfill(dryRun);
    res.json({ success: true, ...result });
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
}));

export default router;
