/**
 * Cache version API routes.
 *
 * GET /api/cache/latest-dates
 *   Returns the latest (MAX) date for each major data source in one payload.
 *   The frontend uses this to decide whether its cached data is stale.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync } from "../lib/http.js";
import { getLatestDates } from "../services/infra/cache.service.js";

const router = Router();

router.get("/latest-dates", wrapAsync(async (_req: Request, res: Response) => {
  res.json(await getLatestDates());
}));

export default router;
