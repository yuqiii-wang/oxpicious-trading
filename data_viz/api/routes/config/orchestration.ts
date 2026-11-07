/**
 * GET /api/config/orchestration — the Orchestration tab (read-only).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { readOrchestrationTiers } from "../../services/orchestration.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Orchestration steps — scripts/orchestration/ tree (read-only)
// ---------------------------------------------------------------------------

/** GET /api/config/orchestration — every tier's step list (tiers in
 *  main_run.sh run order, steps by step number). No write route: the
 *  files are git-reviewed edits. */
router.get("/orchestration", (_req: Request, res: Response) => {
  try {
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      tiers: readOrchestrationTiers(),
    });
  } catch (err) {
    console.error("[config/orchestration GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
