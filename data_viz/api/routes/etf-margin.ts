/**
 * ETF + Margin API routes.
 */
import { Router, type Request, type Response } from "express";
import {
  listThemes,
  getEtfMarginCombined,
} from "../services/etf-margin.service.js";

const router = Router();

router.get("/themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listThemes());
  } catch (err) {
    console.error("[etf-margin/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      // Backward-compat: old 'theme' query param is treated as 'industry' slug.
      ...(typeof req.query.theme === "string" && !req.query.industry
        ? { industry: req.query.theme as string }
        : {}),
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
