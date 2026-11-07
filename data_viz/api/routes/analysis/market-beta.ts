/**
 * Market Beta routes (/market-beta/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getMarketBetaChart,
  getMarketBetaChartByCode,
  listMarketBetaThemes,
  listMarketBetaStrategyThemes,
} from "../../services/analysis/index.js";

const router = Router();

// ---- Market Beta (member index values, rebased to 100 client-side)
//   NO per-code aggregation table — the per-index data is queried directly
//   from stats.index_basic_stats JOIN stats.sec_classification at request
//   time; the precomputed mean/var overlay comes from stats.industry_basic_stats
//   (renamed from analysis.industry_sentiments 2026-08-24, built by
//   builds.industry). The frontend rebases each member index to 100 at the
//   start of the displayed (zoom) window (scale-invariant comparison across
//   indices with different absolute price levels).
//
//   GET /api/analysis/market-beta/themes
//     Returns the L1 sector → L2 industry tree (SectorNode[]) built directly
//     from stats.sec_classification (type='index'). Each industry's chip
//     count = number of member indices in that industry.
//
//   GET /api/analysis/market-beta/chart?industry_id=BANKS
//     Returns per-index close time series for ONE industry. One entry per
//     member index, each with its raw daily close series. The frontend
//     rebases each to 100 at the start of the visible (zoom) window.
router.get("/market-beta/themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listMarketBetaThemes(exchange));
  } catch (err) {
    console.error("[analysis/market-beta/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/market-beta/strategy-themes */
router.get("/market-beta/strategy-themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listMarketBetaStrategyThemes(exchange));
  } catch (err) {
    console.error("[analysis/market-beta/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/market-beta/chart", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    res.json(await getMarketBetaChart(industryId));
  } catch (err) {
    console.error("[analysis/market-beta/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/market-beta/chart-by-code?code=931696
 *  Returns chart data (close series + stock_num) for a single index code.
 *  Used when an L3 index chip is clicked under a strategy/theme —
 *  strategy-primary indices may lack an industry_id classification. */
router.get("/market-beta/chart-by-code", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMarketBetaChartByCode(code));
  } catch (err) {
    console.error("[analysis/market-beta/chart-by-code] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
