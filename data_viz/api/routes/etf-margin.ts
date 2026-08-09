/**
 * ETF + Margin API routes.
 */
import { Router, type Request, type Response } from "express";
import {
  listThemes,
  listStrategyThemes,
  getEtfMarginCombined,
} from "../services/etf-margin.service.js";

const router = Router();

router.get("/themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listThemes(exchange));
  } catch (err) {
    console.error("[etf-margin/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/etf-margin/strategy-themes — parallel L1 strategy → L2 theme tree */
router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listStrategyThemes(exchange));
  } catch (err) {
    console.error("[etf-margin/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      strategy: typeof req.query.strategy === "string" ? req.query.strategy : undefined,
      theme: typeof req.query.theme === "string" ? req.query.theme : undefined,
      code: typeof req.query.code === "string" ? req.query.code : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      limit_per_theme:
        typeof req.query.limit_per_theme === "string"
          ? parseInt(req.query.limit_per_theme, 10)
          : undefined,
      page:
        typeof req.query.page === "string"
          ? parseInt(req.query.page, 10)
          : undefined,
      page_size:
        typeof req.query.page_size === "string"
          ? parseInt(req.query.page_size, 10)
          : undefined,
    };
    res.json(await getEtfMarginCombined(query));
  } catch (err) {
    console.error("[etf-margin/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
