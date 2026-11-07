/**
 * Run one analysis main for ONE security via the shared WSL py-runner:
 * POST /run-analysis + GET /run-analysis/status.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  runPythonModule,
  getPythonProcessStatus,
} from "../../services/py-runner.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Run one analysis main for ONE security (UI per-security build button).
//
//  POST /api/analysis/run-analysis   body: { module, sec_type, code }
//    Spawns `python -m analysis.<module> --sec-type <st> --code <code>` via
//    the shared WSL py-runner and WAITS for it to exit. Deduped by
//    process-id-tag — a second click while a run is in flight resolves
//    immediately with `already_running: true`.
//
//  GET /api/analysis/run-analysis/status?process_id_tag=a,b
//    → { status: { [tag]: boolean } } — running-state of the tags, so a
//    page refresh can put the button straight back into its spinning
//    state until the remote process exits.
// ---------------------------------------------------------------------------

/** Analysis mains that support single-security recomputation (--code). */
const RUNNABLE_ANALYSIS_MODULES = new Set([
  "mov_ave_spread",
  "pe_and_dividends",
]);

router.post("/run-analysis", async (req: Request, res: Response) => {
  try {
    const module = typeof req.body?.module === "string" ? req.body.module : "";
    const secType = typeof req.body?.sec_type === "string" ? req.body.sec_type : "";
    const code = typeof req.body?.code === "string" ? req.body.code : "";
    if (!RUNNABLE_ANALYSIS_MODULES.has(module)) {
      res.status(400).json({
        success: false,
        stderr_tail: `Unknown analysis module '${module}' (supported: ${[...RUNNABLE_ANALYSIS_MODULES].join(", ")})`,
      });
      return;
    }
    if (!code || !secType) {
      res.status(400).json({ success: false, stderr_tail: "Missing 'sec_type' or 'code'" });
      return;
    }
    const tag = `analysis-run:${module}:${secType}:${code}`;
    console.log(
      `[analysis/run-analysis] python -m analysis.${module} --sec-type ${secType} --code ${code}`,
    );
    const result = await runPythonModule(
      `analysis.${module}`,
      ["--sec-type", secType, "--code", code],
      { processIdTag: tag },
    );
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: tag,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/run-analysis] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

router.get("/run-analysis/status", (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.process_id_tag === "string" ? req.query.process_id_tag : "";
    const tags = raw.split(",").map((t) => t.trim()).filter((t) => t.length > 0);
    res.json({ status: getPythonProcessStatus(tags) });
  } catch (err) {
    console.error("[analysis/run-analysis/status] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
