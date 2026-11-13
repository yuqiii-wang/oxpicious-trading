/**
 * Market Beta routes (/market-beta/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getMarketBetaChart,
  getMarketBetaChartByCode,
  getMarketParticipation,
  listMarketBetaThemes,
  listMarketBetaStrategyThemes,
} from "../../services/analysis/index.js";
import { wrapAsync, strParam, intParam } from "../../lib/http.js";

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
router.get("/market-beta/themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listMarketBetaThemes(exchange));
}));

/** GET /api/analysis/market-beta/strategy-themes */
router.get("/market-beta/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listMarketBetaStrategyThemes(exchange));
}));

router.get("/market-beta/chart", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  res.json(await getMarketBetaChart(industryId));
}));

/** GET /api/analysis/market-beta/chart-by-code?code=931696
 *  Returns chart data (close series + stock_num) for a single index code.
 *  Used when an L3 index chip is clicked under a strategy/theme —
 *  strategy-primary indices may lack an industry_id classification. */
router.get("/market-beta/chart-by-code", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getMarketBetaChartByCode(code));
}));

/** GET /api/analysis/market-beta/participation?days=260
 *  The Market Participation panel series: latest N trading days of
 *  analysis.market_participation (whole-market turnover / SSE share /
 *  advance-decline breadth), oldest first. */
router.get("/market-beta/participation", wrapAsync(async (req: Request, res: Response) => {
  const days = intParam(req, "days", { required: true, min: 1, max: 5000, strict: true });
  res.json(await getMarketParticipation(days));
}));

export default router;
