/**
 * Market Crowdedness routes (/market-crowdedness/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listMarketCrowdednessCodes,
  getMarketCrowdednessChart,
  getMarketCrowdednessHotTech,
  listMarketCrowdednessHotYears,
  listMarketCrowdednessThemes,
  listMarketCrowdednessStrategyThemes,
} from "../../services/analysis/index.js";
import { parseSecType, parseCode, parseExchange } from "./_shared.js";
import { wrapAsync, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Market Crowdedness (analysis.market_crowdedness)
//   Per-(code, sec_type, date) crowdedness curve for indices and
//   composite industries, built by analysis.market_crowdedness — the
//   percentile-normalized multi-pillar score (0.30 concentration /
//   0.30 flow / 0.25 co-movement / 0.15 valuation vs the pool's own
//   250-day history) over the pool's stable hot set (top 10% by
//   60-active-day mean amount, re-ranked daily). The nav/codes
//   universe is the FULL classification (stats.sec_classification
//   type='index' + stats.industry_identity entities) — deliberately NOT
//   restricted to codes with rows; other codes plot close + amount
//   only.
//
//   GET /api/analysis/market-crowdedness/codes?sec_type=index&exchange=SS
//     Returns MarketCrowdednessCodesResponse: the flat (code, name)
//     universe for the page's codes shell (index | industry).
//   GET /api/analysis/market-crowdedness/themes?sec_type=index
//     L1 sector → L2 industry → items tree for SecClassificationNav.
//   GET /api/analysis/market-crowdedness/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/market-crowdedness/chart?sec_type=index&code=000001
//                                                          [&hot_year=2025]
//     Returns MarketCrowdednessChartResponse: daily (date, close,
//     trading_amount, crowdedness_score, the raw pillars + percentiles,
//     evidence, top_stocks) rows for one security — metric fields NULL
//     on dates with no defined crowdedness row. Optional hot_year (a
//     calendar year) swaps in the YEAR-PINNED variant from
//     analysis.market_crowdedness_year: the hot basket frozen to that
//     year's roster, applied across the whole history (a member
//     contributes only on dates it traded).
//   GET /api/analysis/market-crowdedness/hot-years?sec_type=index&code=000001
//     Returns MarketCrowdednessHotYearsResponse: the pinned variants
//     available for the code (years, newest first) — the dropdown
//     source.
//   GET /api/analysis/market-crowdedness/hot-tech?sec_type=index&code=000001
//     Returns MarketCrowdednessHotTechResponse: daily (date, close,
//     hot_n, mean/median + mean_pct/median_pct of the eight hot-tech
//     metrics) rows for one security — the hot stocks' typical
//     technical profile from analysis.market_crowdedness_hot_tech over
//     the day's rolling hot set; metric fields NULL on dates with no
//     hot-tech row (rolling only — no hot_year param).
router.get("/market-crowdedness/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listMarketCrowdednessCodes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/market-crowdedness/themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listMarketCrowdednessThemes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/market-crowdedness/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listMarketCrowdednessStrategyThemes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/market-crowdedness/chart", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  const hotYear = typeof req.query.hot_year === "string"
    ? req.query.hot_year
    : undefined;
  res.json(await getMarketCrowdednessChart(
    code, parseSecType(req), hotYear,
  ));
}));

router.get("/market-crowdedness/hot-years", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await listMarketCrowdednessHotYears(
    code, parseSecType(req),
  ));
}));

router.get("/market-crowdedness/hot-tech", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await getMarketCrowdednessHotTech(
    code, parseSecType(req),
  ));
}));

export default router;
