/**
 * Index Baseline API routes.
 */
import { Router, type Request, type Response } from "express";
import { listIndices, getIndexBaseline, getIndexIntraday5min } from "../services/index-baseline.service.js";

const router = Router();

/** GET /api/index-baseline/list — list all available indices */
router.get("/list", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndices());
  } catch (err) {
    console.error("[index-baseline/list] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/index-baseline/combined?code=000001&start_date=&end_date= */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const startDate = typeof req.query.start_date === "string" ? req.query.start_date : undefined;
    const endDate = typeof req.query.end_date === "string" ? req.query.end_date : undefined;
    res.json(await getIndexBaseline(code, startDate, endDate));
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
