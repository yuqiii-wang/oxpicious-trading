/**
 * Signal Rules Audit routes (/signals-audit*) — the per-family census
 * and the pairwise family overlap of the standalone signal history,
 * including the fixed process-id-tag for the run endpoint. Full route
 * docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getSignalAuditMeta,
  getSignalOverlap,
  getSignalTypeSummary,
} from "../../services/analysis/index.js";
import { runPythonModule } from "../../services/infra/py-runner.service.js";
import { wrapAsync, strParam } from "../../lib/http.js";

const router = Router();

// ---- Signal Rules Audit (composite analysis — drives the Composites page).
//   GET /api/analysis/signals-audit/meta?sec_type=index
//     Returns SignalAuditMetaResponse: the YEAR periods present in the
//     audit at the scope with bounds / signal universe / family + event
//     totals — the page's period selector basis.
//   GET /api/analysis/signals-audit/summary?sec_type=index&period=<year|all>
//     Returns SignalTypeAuditResponse: one row per family — trigger-day
//     counts, buy/sell split, code/day spread, universe share.
//   GET /api/analysis/signals-audit/overlap?sec_type=index&period=<year|all>
//     Returns SignalOverlapAuditResponse: one row per ORDERED family pair
//     (A = base row, B = other column) — overlap_pct / expected_pct /
//     lift / jaccard / same-vs-opposite action splits. "all" sums the
//     stored years (counts additive, ratios re-derived).
//   POST /api/analysis/signals-audit/run
//     Spawns `python -m analysis.signal_rules_audit` (incremental:
//     missing + trailing + grace years) via the shared WSL py-runner and
//     WAITS for it to exit. Deduped by a fixed process-id-tag; running
//     state polled via GET /api/analysis/run-analysis/status with
//     SIGNAL_RULES_AUDIT_RUN_TAG.
router.get("/signals-audit/meta", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getSignalAuditMeta(strParam(req, "sec_type")));
}));

router.get("/signals-audit/summary", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getSignalTypeSummary(strParam(req, "sec_type"), strParam(req, "period")));
}));

router.get("/signals-audit/overlap", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getSignalOverlap(strParam(req, "sec_type"), strParam(req, "period")));
}));

/** Fixed process-id-tag for UI-triggered signal-rules-audit runs — one
 *  refresh at a time globally (must match the tag built client-side). */
export const SIGNAL_RULES_AUDIT_RUN_TAG = "analysis-run:signal_rules_audit";

router.post("/signals-audit/run", wrapAsync(async (_req: Request, res: Response) => {
  console.log("[analysis/signals-audit/run] python -m analysis.signal_rules_audit");
  const result = await runPythonModule("analysis.signal_rules_audit", [], {
    processIdTag: SIGNAL_RULES_AUDIT_RUN_TAG,
  });
  res.json({
    success: result.success,
    already_running: result.already_running === true,
    process_id_tag: SIGNAL_RULES_AUDIT_RUN_TAG,
    stdout_tail: result.stdout.slice(-2000),
    stderr_tail: result.stderr.slice(-2000),
  });
}));

export default router;
