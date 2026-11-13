/**
 * Composites / industry attribution routes (/pair-pct-ratios*,
 * /industry-benchmark-attribution, /industry-attribution/*,
 * /industry-hypes-and-drains, /industry-etf-contribution/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listPairPctRatioUniverse,
  getPairPctRatios,
  getIndustryBenchmarkAttribution,
  listIndustryAttributionBenchmarks,
  getBenchmarkPriceChart,
  getIndustryAttributionPriceSeries,
  getAllIndustriesAttribution,
  getMemberIndexAttribution,
  getIndustryEtfPriceSeries,
  getIndustryEtfContributionBars,
  getIndustryHypesAndDrains,
  getIndustryStockAttribution,
} from "../../services/analysis/index.js";
import { wrapAsync, strParam, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Pair Pct-Change Ratios (industry x industry excess pct change
//      on the stride-20 global-calendar grid — the trailing mean of the
//      daily pct-change difference; drives the pair
//      selector + ratio chart on the Composites → Opposite Industry
//      Correlations page).
//   GET /api/analysis/pair-pct-ratios/universe?interval=20
//         [&industry_ids=DIV_GENERAL,TECH_GENERAL]
//     Returns PairPctRatioUniverseResponse: both sides of the pair
//     selector — subject INDUSTRIES and benchmark INDUSTRIES (the pair
//     is industry x industry; grain is 'industry' on every entry), each
//     with label, counterpart count and grid date bounds. With
//     industry_ids, both sides are limited to those industry_ids (the
//     page scopes the selector to its already-selected industries).
//   GET /api/analysis/pair-pct-ratios?code=BROAD_SSE&benchmark=BROAD_CSI300
//         &interval=20&grain=industry
//     Returns PairPctRatiosResponse: the four stride-grid EXCESS
//     pct-change values (5/20/60/252d — the trailing mean of the daily
//     difference ind_pct(code) − ind_pct(benchmark), in pct points) for
//     ONE industry x industry pair, ordered by date (grain is always
//     'industry').
router.get("/pair-pct-ratios/universe", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "interval") ?? "";
  const rawIndustries = strParam(req, "industry_ids") ?? "";
  const industryIds = rawIndustries
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  res.json(await listPairPctRatioUniverse(raw, industryIds));
}));

router.get("/pair-pct-ratios", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code") ?? "";
  const benchmark = strParam(req, "benchmark") ?? "";
  const interval = strParam(req, "interval") ?? "";
  const grain = strParam(req, "grain") ?? "";
  const market = strParam(req, "market") ?? "";
  if (!code || !benchmark) {
    throw new HttpError(400, "MISSING_PARAM", "Both 'code' and 'benchmark' are required");
  }
  if (code === benchmark) {
    throw new HttpError(400, "INVALID_PARAM", "'code' and 'benchmark' must differ (self-pairs excluded)");
  }
  res.json(await getPairPctRatios(code, benchmark, interval, grain, market));
}));

// ---- Industry-level Benchmark Attribution (aggregated
//      stats.cross_stats pair rows per industry_id). Drives the "Benchmark
//      Attribution" view on the MarketBeta page — the toggle that
//      swaps the price/correlation plot for a fluctuation-attribution bar
//      chart per industry. Aggregates per-index rows to one row per
//      (industry_id, benchmark_code, date).
//   GET /api/analysis/industry-benchmark-attribution?industry_id=BANKS&date=YYYY-MM-DD
//     Returns IndustryBenchmarkAttributionResponse: one row per benchmark
//     with avg shared_weight, avg rolling corr (5/20/60/252d), summed ETF
//     trading_amount, benchmark_return (computed on-the-fly), and
//     avg_subject_return. `date` is optional (defaults to latest available).
router.get("/industry-benchmark-attribution", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const date = rawDate || null;
  res.json(await getIndustryBenchmarkAttribution(industryId, date));
}));

// ---- Industry Attribution Benchmark list + price chart. Drives the
//      benchmark dropdown and the 1st plot (benchmark price chart,
//      clickable to pick a date) in "Benchmark Attribution" mode on the
//      Market Beta page.
//   GET /api/analysis/industry-attribution/benchmarks
//     Returns IndustryAttributionBenchmarksResponse: list of all benchmark
//     codes that appear in analysis.industry_attributions, enriched with
//     display name and is_broad_market flag. Broad-market benchmarks first.
//   GET /api/analysis/industry-attribution/benchmark-price?code=000300
//     Returns BenchmarkPriceChartResponse: daily close + fractional daily
//     return series for ONE benchmark index (from stats.index_basic_stats).
router.get("/industry-attribution/benchmarks", wrapAsync(async (_req: Request, res: Response) => {
  const benchmarks = await listIndustryAttributionBenchmarks();
  res.json({ benchmarks });
}));

router.get("/industry-attribution/benchmark-price", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getBenchmarkPriceChart(code));
}));

//   GET /api/analysis/industry-attribution/non-this-industry-price
//     ?industry_id=BANKS&benchmark_code=000300
//   Returns IndustryAttributionPriceSeriesResponse: benchmark close +
//   benchmark_rolling + non_this_industry_price + 5 rolling_Xdays_price
//   columns (5/20/60/252/504) for ONE (industry_id, benchmark_code) pair.
//   Drives the green/red shade overlay on the BenchmarkPriceChart. The
//   frontend dropdown picks which rolling window drives the shade.
router.get("/industry-attribution/non-this-industry-price", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  const benchmarkCode = strParam(req, "benchmark_code", { required: true });
  res.json(await getIndustryAttributionPriceSeries(industryId, benchmarkCode));
}));

//   GET /api/analysis/industry-attribution/all-industries
//     ?benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
//   Returns AllIndustriesAttributionResponse: one row per industry with
//   benchmark_shared_weight + industry_shared_weight for the given
//   (benchmark_code, date). Drives the industry-level bar chart.
router.get("/industry-attribution/all-industries", wrapAsync(async (req: Request, res: Response) => {
  const benchmarkCode = strParam(req, "benchmark_code", { required: true });
  const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
  res.json(await getAllIndustriesAttribution(benchmarkCode, date || null));
}));

// ---- Industry Hypes & Drains — pre-computed top-5 (HYPE) + bottom-5
//      (DRAIN) industries ranked by attribution contribution to a COMPOSITE
//      broad-market benchmark (MAIN=SS+SZ, INNOV=GEM+STAR). Drives the
//      "Hypes & Drains" sub-toggle in "Market Trend" mode on the Industry
//      Sentiments page.
//   GET /api/analysis/industry-hypes-and-drains
//     ?benchmark_code=000300&period_days=120&weighting=equal
//     Returns ALL seasonal (monthly) rankings + benchmark price series
//     + each ranked industry's full daily rolling price series.
//     weighting: 'equal' (raw attribution contribution) or 'amt'
//     (contribution × shared_trading_amt). Default: 'equal'.
router.get("/industry-hypes-and-drains", wrapAsync(async (req: Request, res: Response) => {
  const benchmarkCode = strParam(req, "benchmark_code") ?? "";
  const periodDays = strParam(req, "period_days") ?? "120";
  const weighting = strParam(req, "weighting") ?? "equal";
  res.json(await getIndustryHypesAndDrains(benchmarkCode, periodDays, weighting));
}));

//   GET /api/analysis/industry-attribution/member-indices
//     ?industry_id=BANKS&benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
//   Returns MemberIndexAttributionResponse: one row per member index with
//   code_sec_shared_weight + benchmark_sec_shared_weight for the given
//   (industry_id, benchmark_code, date). Drives the per-industry bar charts.
router.get("/industry-attribution/member-indices", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  const benchmarkCode = strParam(req, "benchmark_code", { required: true });
  const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
  res.json(await getMemberIndexAttribution(industryId, benchmarkCode, date || null));
}));

// ---- Industry ETF Contribution — mirrors "Benchmark Attribution" but with
//      ETFs as the unit of analysis instead of benchmark indices. Drives the
//      "ETF Contribution" view on the Market Beta page.
//   GET /api/analysis/industry-etf-contribution/etf-price?industry_ids=BANKS,AI
//     Returns IndustryEtfPriceSeriesResponse: daily close series for ALL ETFs
//     tracking member indices of the selected industries. The frontend
//     rebases each ETF to 100 at its own first date (cascading so later ETFs
//     start at the mean of already-active ETFs). Drives the 1st plot.
//   GET /api/analysis/industry-etf-contribution/etf-bars
//     ?industry_id=BANKS&date=YYYY-MM-DD (date optional → latest)
//     Returns IndustryEtfContributionBarsResponse: one row per ETF with
//     trading_amount + etf_return, plus the industry aggregate from
//     analysis.industry_etf_contribution. Drives the 2nd+ plots.
router.get("/industry-etf-contribution/etf-price", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "industry_ids") ?? "";
  const industryIds = raw.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
  if (industryIds.length === 0) {
    throw new HttpError(400, "MISSING_PARAM", "Missing 'industry_ids' parameter (comma-separated)");
  }
  res.json(await getIndustryEtfPriceSeries(industryIds));
}));

router.get("/industry-etf-contribution/etf-bars", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const date = rawDate || null;
  res.json(await getIndustryEtfContributionBars(industryId, date));
}));

// ---- Industry Stock Attribution — the per-industry daily pct-change
//      bucket distribution (the rise/drop audit). Drives the
//      "Stock Attribution" view on the Market Beta page: one histogram
//      per selected industry, each bar = one of the 12
//      'stock_change_bucket' buckets ("how many stocks rose >= 7.5%").
//   GET /api/analysis/industry-stock-attribution
//     ?industry_id=BANKS&date=YYYY-MM-DD (date optional → latest)
//     Returns IndustryStockAttributionResponse: the 12 buckets with
//     stock_count + pct_of_industry, the denormalized total_stocks, and
//     the 90 most recent available dates (the view's date dropdown).
router.get("/industry-stock-attribution", wrapAsync(async (req: Request, res: Response) => {
  const industryId = strParam(req, "industry_id", { required: true });
  const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const date = rawDate || null;
  res.json(await getIndustryStockAttribution(industryId, date));
}));

export default router;
