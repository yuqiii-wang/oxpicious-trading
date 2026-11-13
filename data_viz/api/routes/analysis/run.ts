/**
 * Run one analysis main for ONE security via the shared WSL py-runner:
 * POST /run-analysis + GET /run-analysis/status.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  runPythonModule,
  getPythonProcessStatus,
} from "../../services/infra/py-runner.service.js";
import { wrapAsync, strParam, HttpError } from "../../lib/http.js";

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

router.post("/run-analysis", wrapAsync(async (req: Request, res: Response) => {
  const module = typeof req.body?.module === "string" ? req.body.module : "";
  const secType = typeof req.body?.sec_type === "string" ? req.body.sec_type : "";
  const code = typeof req.body?.code === "string" ? req.body.code : "";
  if (!RUNNABLE_ANALYSIS_MODULES.has(module)) {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `Unknown analysis module '${module}' (supported: ${[...RUNNABLE_ANALYSIS_MODULES].join(", ")})`,
    );
  }
  if (!code || !secType) {
    throw new HttpError(400, "MISSING_PARAM", "Missing 'sec_type' or 'code'");
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
}));

// Running-state now resolves through the py-runner's cluster-backed
// registry (the analysis.* tags live in the analyzer cluster server), so
// the handler is async like the other status endpoints.
router.get("/run-analysis/status", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "process_id_tag") ?? "";
  const tags = raw.split(",").map((t) => t.trim()).filter((t) => t.length > 0);
  res.json({ status: await getPythonProcessStatus(tags) });
}));

export default router;
