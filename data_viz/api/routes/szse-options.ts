/**
 * SZSE Options API routes.
 */
import { Router, type Request, type Response } from "express";
import {
  listUnderlyings,
  getOptionsCombined,
  getEtfOhlcv,
  getOptionsExpiryGaps,
  getOptionsSkewnessCorr,
} from "../services/szse-options.service.js";

const router = Router();

router.get("/underlyings", async (req: Request, res: Response) => {
  try {
    const targetType =
      typeof req.query.target_type === "string" ? req.query.target_type : undefined;
    res.json(await listUnderlyings(targetType));
  } catch (err) {
    console.error("[szse-options/underlyings] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      underlying: typeof req.query.underlying === "string" ? req.query.underlying : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      target_type: typeof req.query.target_type === "string" ? req.query.target_type : undefined,
    };
    res.json(await getOptionsCombined(query));
  } catch (err) {
    console.error("[szse-options/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/etf-ohlcv", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' query parameter" });
      return;
    }
    const data = await getEtfOhlcv(
      code,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      typeof req.query.target_type === "string" ? req.query.target_type : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/etf-ohlcv] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/stats-before-expiry", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const data = await getOptionsExpiryGaps(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/stats-before-expiry] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/skewness-corr", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const data = await getOptionsSkewnessCorr(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/skewness-corr] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
