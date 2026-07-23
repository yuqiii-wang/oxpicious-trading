/**
 * Cache version API routes.
 *
 * GET /api/cache/latest-dates
 *   Returns the latest (MAX) date for each major data source in one payload.
 *   The frontend uses this to decide whether its cached data is stale.
 */
import { Router, type Request, type Response } from "express";
import { getLatestDates } from "../services/cache.service.js";

const router = Router();

router.get("/latest-dates", async (_req: Request, res: Response) => {
  try {
    res.json(await getLatestDates());
  } catch (err) {
    console.error("[cache/latest-dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
