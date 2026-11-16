/**
 * GET /api/config/orchestration — the Orchestration tab (read-only).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { readOrchestrationTiers } from "../../services/infra/orchestration.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Orchestration steps — scripts/orchestration/ tree (read-only)
// ---------------------------------------------------------------------------

/** GET /api/config/orchestration — every tier's step list (tiers in the
 *  nightly chain order — the maintainer's NIGHTLY_CHAIN, steps by step
 *  number). No write route: the files are git-reviewed edits. */
router.get("/orchestration", (_req: Request, res: Response) => {
  res.json({
    success: true,
    generatedAt: new Date().toISOString(),
    tiers: readOrchestrationTiers(),
  });
});

export default router;
