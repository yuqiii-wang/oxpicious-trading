/**
 * POST /orchestration/run — manual orchestration tier run via the server
 * cluster's monitor area (the serial step runner inside
 * server_cluster/monitor/orchestration.py — the server-side
 * run_steps.sh).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  monitorOrchestrationFiles,
  monitorOrchestrationPlan,
  monitorOrchestrationStatus,
  monitorRunOrchestration,
} from "../../services/infra/monitor-client.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Orchestration tier run — the monitor area's serial runner (the
//  Orchestration config tab's title Run button)
// ---------------------------------------------------------------------------

/** POST /api/monitor/orchestration/run { file, dryRun? } — starts ONE
 *  orchestration step file's tier on the monitor area (POST
 *  /orchestration/run): the steps run one by one, in numeric key order,
 *  each submitted through the monitor area's job registry — so the
 *  tier's steps show up on the Pipeline processes table as they start,
 *  and GET /api/monitor/orchestration/runs/:tag polls their statuses.
 *  dryRun=true runs nothing: the plan is echoed from the monitor area
 *  (the former DRY_RUN=1 knob) — verification / smoke only. A second run
 *  of the SAME tier while one is in flight is refused by the monitor
 *  area (409 → surfaced here as alreadyRunning). */
router.post("/orchestration/run", wrapAsync(async (req: Request, res: Response) => {
  const file = typeof req.body?.file === "string" ? req.body.file.trim() : "";
  const dryRun = req.body?.dryRun === true;
  if (!/^[A-Za-z0-9_-]+\.json$/.test(file)) {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `file must be an orchestration step file name like builds_daily.json, got '${file}'`,
    );
  }
  const tier = file.replace(/\.json$/, "");
  if (dryRun) {
    // Echo the plan only (the former DRY_RUN=1: verification / smoke,
    // never surfaced in the UI).
    const plan = await monitorOrchestrationPlan(tier).catch((err) => {
      throw new HttpError(
        502,
        "UPSTREAM_FAILED",
        err instanceof Error ? err.message : String(err),
      );
    });
    res.json({
      success: true,
      started: false,
      dryRun: true,
      tier,
      steps: plan.steps,
      message: `dry run — plan only (${plan.steps.length} step(s)), nothing executed`,
    });
    return;
  }
  try {
    const run = await monitorRunOrchestration(tier);
    res.json({
      success: true,
      started: true,
      tier,
      tag: run.tag,
      steps: run.steps,
      message: `monitor area is running ${tier} (${run.steps.length} step(s), one by one) — poll the tag for step statuses`,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("409") || msg.toLowerCase().includes("already")) {
      res.json({
        success: true,
        started: true,
        alreadyRunning: true,
        tier,
        message: `${tier} is already running on the monitor area — deduped, no new run`,
      });
      return;
    }
    throw new HttpError(502, "UPSTREAM_FAILED", msg);
  }
}));

/** GET /api/monitor/orchestration/runs/:tag — one tier run's step
 *  statuses (the UI's progress poll for a Run started here). */
router.get("/orchestration/runs/:tag", wrapAsync(async (req: Request, res: Response) => {
  try {
    const status = await monitorOrchestrationStatus(req.params.tag);
    res.json({ success: true, ...status });
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
}));

/** GET /api/monitor/orchestration — the orchestration tier names the
 *  monitor area serves (the config tab's file list source of record). */
router.get("/orchestration", wrapAsync(async (_req: Request, res: Response) => {
  try {
    const files = await monitorOrchestrationFiles();
    res.json({ success: true, files });
  } catch (err) {
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      err instanceof Error ? err.message : String(err),
    );
  }
}));

export default router;
