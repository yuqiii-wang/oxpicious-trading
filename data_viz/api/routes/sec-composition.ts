/**
 * Sec Composition API routes.
 */
import { Router, type Request, type Response } from "express";
import { getSecComposition, getQuarterlyComposition, getLinkedEtfs, getSimilarIndices, getIndustryWeightSeries } from "../services/sec-composition.service.js";

const router = Router();

/** GET /api/sec-composition?code=159001.SZ[&date=2026-06-30]
 *  Without `date`: latest snapshot. With `date`: latest snapshot within the
 *  calendar quarter containing the date (the "by season" lookup). */
router.get("/", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date : undefined;
    res.json(await getSecComposition(code, date));
  } catch (err) {
    console.error("[sec-composition] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/quarterly?code=159673
 *  Per-quarter industry-aggregated composition (latest snapshot within each
 *  quarter; quarters without a snapshot are absent). Falls back to the
 *  tracking index when the ETF has no snapshots. */
router.get("/quarterly", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getQuarterlyComposition(code));
  } catch (err) {
    console.error("[sec-composition/quarterly] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/industry-weight-series?code=159673&industry_id=BANKS
 *  ONE industry's weight in the security's composition across ALL snapshot
 *  dates (roughly monthly; denser than the quarterly view). Falls back to the
 *  tracking index when the ETF has no snapshots. Used by the ETF Holdings
 *  page's Industry-changes row drill-down. */
router.get("/industry-weight-series", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    const industryId =
      typeof req.query.industry_id === "string" ? req.query.industry_id : "";
    if (!code || !industryId) {
      res.status(400).json({ error: "Missing 'code' or 'industry_id' parameter" });
      return;
    }
    res.json(await getIndustryWeightSeries(code, industryId));
  } catch (err) {
    console.error("[sec-composition/industry-weight-series] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/linked-etfs?code=000300
 *  Returns the ETFs tracking the given index (parent_index_code = code),
 *  enriched with latest close + n_days from stats.v_etf_margin. */
router.get("/linked-etfs", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getLinkedEtfs(code));
  } catch (err) {
    console.error("[sec-composition/linked-etfs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/sec-composition/similar-indices?code=000300
 *  Returns the top-3 similar indices by mutual shared composition weight for
 *  the given index, from stats.sec_similars (sec_type='index', latest snapshot <= today). */
router.get("/similar-indices", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getSimilarIndices(code));
  } catch (err) {
    console.error("[sec-composition/similar-indices] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
