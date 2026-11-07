/**
 * GET /api/config/keyword-maps — the Keywords tab (read-only).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { listKeywordMaps } from "../../services/keyword-maps.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Keywords tab — static.keyword_maps (the seeded keyword → category map)
// ---------------------------------------------------------------------------

/** GET /api/config/keyword-maps — every seeded keyword row, labels decoded
 *  at the boundary (category_type / category / parts_of_speech / language).
 *  READ-ONLY: the map is a static seed table (rows land only via the SQL
 *  seed files under database/sql/static/keyword_maps/) — editing means
 *  editing the seed file and re-applying, like every other static config. */
router.get("/keyword-maps", async (_req: Request, res: Response) => {
  try {
    const entries = await listKeywordMaps();
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      count: entries.length,
      entries,
    });
  } catch (err) {
    console.error("[config/keyword-maps GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
