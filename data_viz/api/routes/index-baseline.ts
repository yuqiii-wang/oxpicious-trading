/**
 * Index Baseline API routes.
 */
import { Router, type Request, type Response } from "express";
import {
  listIndices,
  getIndexIntraday5min,
  listIndexThemes,
  getIndicesCombined,
} from "../services/index-baseline.service.js";

const router = Router();

/** GET /api/index-baseline/list — list all available indices (flat list) */
router.get("/list", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndices());
  } catch (err) {
    console.error("[index-baseline/list] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/index-baseline/themes — two-level L1 sector → L2 industry tree */
router.get("/themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndexThemes());
  } catch (err) {
    console.error("[index-baseline/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/index-baseline/combined?sector=&industry=&page=&page_size=
 *  Paginated indices filtered by L1 sector + L2 industry. */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      code: typeof req.query.code === "string" ? req.query.code : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      page:
        typeof req.query.page === "string"
          ? parseInt(req.query.page, 10)
          : undefined,
      page_size:
        typeof req.query.page_size === "string"
          ? parseInt(req.query.page_size, 10)
          : undefined,
    };
    res.json(await getIndicesCombined(query));
  } catch (err) {
    console.error("[index-baseline/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/index-baseline/intraday-5min?code=000001&date=2026-07-21 */
router.get("/intraday-5min", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    const date = typeof req.query.date === "string" ? req.query.date : "";
    if (!code || !date) {
      res.status(400).json({ error: "Missing 'code' or 'date' parameter" });
      return;
    }
    res.json(await getIndexIntraday5min(code, date));
  } catch (err) {
    console.error("[index-baseline/intraday-5min] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
