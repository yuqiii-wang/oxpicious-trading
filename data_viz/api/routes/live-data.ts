/**
 * Live Data API routes.
 *
 * Three endpoints:
 *   GET /api/live-data/dates?type=index|stock
 *     → { type, dates: ["2026-07-31", ...] }  (descending — most recent first)
 *
 *   GET /api/live-data/themes?type=index|stock
 *     → L1 sector → L2 industry tree (SectorNode[]) restricted to codes with
 *       at least one intraday bar.
 *
 *   GET /api/live-data/combined?type=index|stock&date=&sector=&industry=
 *       &exchange=&code=&page=&page_size=
 *     → paginated list of codes with their intraday bars for the requested
 *       date (defaults to the latest available date).
 *
 * ETF ('type=etf') is intentionally NOT supported here — no
 * stats.etf_intraday_5min table exists yet. The frontend renders an empty
 * placeholder for the ETF tab until that table is added.
 */
import { Router, type Request, type Response } from "express";
import {
  listLiveDataDates,
  listLiveDataThemes,
  getLiveDataCombined,
  type LiveDataSecType,
} from "../services/live-data.service.js";

const router = Router();

function parseSecType(v: unknown): LiveDataSecType {
  return v === "stock" ? "stock" : "index";
}

/** GET /api/live-data/dates?type=index */
router.get("/dates", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    res.json(await listLiveDataDates(secType));
  } catch (err) {
    console.error("[live-data/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/themes?type=index */
router.get("/themes", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    res.json(await listLiveDataThemes(secType));
  } catch (err) {
    console.error("[live-data/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/combined?type=index&date=&sector=&industry=... */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
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
  } catch (err) {
    console.error("[live-data/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
