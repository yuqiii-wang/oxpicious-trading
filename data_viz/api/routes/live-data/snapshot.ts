/**
 * GET /dates, /themes, /strategy-themes, /combined — the Live Data
 * snapshot queries (available dates, sector tree, strategy→theme tree,
 * paginated combined intraday bars).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { wrapAsync } from "../../lib/http.js";
import {
  listLiveDataDates,
  listLiveDataThemes,
  listStrategyThemes,
  getLiveDataCombined,
  type LiveDataSecType,
} from "../../services/market/live-data.service.js";

const router = Router();

function parseSecType(v: unknown): LiveDataSecType {
  return v === "stock" ? "stock" : "index";
}

/** GET /api/live-data/dates?type=index */
router.get("/dates", wrapAsync(async (req: Request, res: Response) => {
  const secType = parseSecType(req.query.type);
  res.json(await listLiveDataDates(secType));
}));

/** GET /api/live-data/themes?type=index */
router.get("/themes", wrapAsync(async (req: Request, res: Response) => {
  const secType = parseSecType(req.query.type);
  // exchange etc. are echoed/passed untrimmed — the inline parse stays.
  const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
  res.json(await listLiveDataThemes(secType, exchange));
}));

/** GET /api/live-data/strategy-themes?type=index — parallel strategy → theme tree */
router.get("/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  const secType = parseSecType(req.query.type);
  const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
  res.json(await listStrategyThemes(secType, exchange));
}));

/** GET /api/live-data/combined?type=index&date=&sector=&industry=... */
router.get("/combined", wrapAsync(async (req: Request, res: Response) => {
  const secType = parseSecType(req.query.type);
  // Filters echo/passthrough untrimmed and page/page_size use parseInt
  // semantics (parseInt("12.9") = 12) — the inline parses stay.
  const query = {
    sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
    industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
    strategy: typeof req.query.strategy === "string" ? req.query.strategy : undefined,
    theme: typeof req.query.theme === "string" ? req.query.theme : undefined,
    code: typeof req.query.code === "string" ? req.query.code : undefined,
    exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
    date: typeof req.query.date === "string" ? req.query.date : undefined,
    page:
      typeof req.query.page === "string"
        ? parseInt(req.query.page, 10)
        : undefined,
    page_size:
      typeof req.query.page_size === "string"
        ? parseInt(req.query.page_size, 10)
        : undefined,
  };
  res.json(await getLiveDataCombined(secType, query));
}));

export default router;
