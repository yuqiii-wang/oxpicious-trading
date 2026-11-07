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
} from "../../services/analysis/index.js";

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
//     pct-change values (5/20/60/255d — the trailing mean of the daily
//     difference ind_pct(code) − ind_pct(benchmark), in pct points) for
//     ONE industry x industry pair, ordered by date (grain is always
//     'industry').
router.get("/pair-pct-ratios/universe", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.interval === "string" ? req.query.interval : "";
    const rawIndustries = typeof req.query.industry_ids === "string"
      ? req.query.industry_ids
      : "";
    const industryIds = rawIndustries
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    res.json(await listPairPctRatioUniverse(raw, industryIds));
  } catch (err) {
    console.error("[analysis/pair-pct-ratios/universe] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/pair-pct-ratios", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    const benchmark = typeof req.query.benchmark === "string" ? req.query.benchmark.trim() : "";
    const interval = typeof req.query.interval === "string" ? req.query.interval : "";
    const grain = typeof req.query.grain === "string" ? req.query.grain.trim() : "";
    const market = typeof req.query.market === "string" ? req.query.market.trim() : "";
    if (!code || !benchmark) {
      res.status(400).json({ error: "Both 'code' and 'benchmark' are required" });
      return;
    }
    if (code === benchmark) {
      res.status(400).json({ error: "'code' and 'benchmark' must differ (self-pairs excluded)" });
      return;
    }
    res.json(await getPairPctRatios(code, benchmark, interval, grain, market));
  } catch (err) {
    console.error("[analysis/pair-pct-ratios] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry-level Benchmark Attribution (aggregated
//      stats.cross_stats pair rows per industry_id). Drives the "Benchmark
//      Attribution" view on the MarketBeta page — the toggle that
//      swaps the price/correlation plot for a fluctuation-attribution bar
//      chart per industry. Aggregates per-index rows to one row per
//      (industry_id, benchmark_code, date).
//   GET /api/analysis/industry-benchmark-attribution?industry_id=BANKS&date=YYYY-MM-DD
//     Returns IndustryBenchmarkAttributionResponse: one row per benchmark
//     with avg shared_weight, avg rolling corr (5/20/60/255d), summed ETF
//     trading_amount, benchmark_return (computed on-the-fly), and
//     avg_subject_return. `date` is optional (defaults to latest available).
router.get("/industry-benchmark-attribution", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
    const date = rawDate || null;
    res.json(await getIndustryBenchmarkAttribution(industryId, date));
  } catch (err) {
    console.error("[analysis/industry-benchmark-attribution] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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
router.get("/industry-attribution/benchmarks", async (_req: Request, res: Response) => {
  try {
    const benchmarks = await listIndustryAttributionBenchmarks();
    res.json({ benchmarks });
  } catch (err) {
    console.error("[analysis/industry-attribution/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-attribution/benchmark-price", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getBenchmarkPriceChart(code));
  } catch (err) {
    console.error("[analysis/industry-attribution/benchmark-price] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/industry-attribution/non-this-industry-price
//     ?industry_id=BANKS&benchmark_code=000300
//   Returns IndustryAttributionPriceSeriesResponse: benchmark close +
//   benchmark_rolling + non_this_industry_price + 5 rolling_Xdays_price
//   columns (5/20/60/255/500) for ONE (industry_id, benchmark_code) pair.
//   Drives the green/red shade overlay on the BenchmarkPriceChart. The
//   frontend dropdown picks which rolling window drives the shade.
router.get("/industry-attribution/non-this-industry-price", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim() : "";
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim() : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    res.json(await getIndustryAttributionPriceSeries(industryId, benchmarkCode));
  } catch (err) {
    console.error("[analysis/industry-attribution/non-this-industry-price] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/industry-attribution/all-industries
//     ?benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
//   Returns AllIndustriesAttributionResponse: one row per industry with
//   benchmark_shared_weight + industry_shared_weight for the given
//   (benchmark_code, date). Drives the industry-level bar chart.
router.get("/industry-attribution/all-industries", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim() : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getAllIndustriesAttribution(benchmarkCode, date || null));
  } catch (err) {
    console.error("[analysis/industry-attribution/all-industries] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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
router.get("/industry-hypes-and-drains", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim() : "";
    const periodDays = typeof req.query.period_days === "string"
      ? req.query.period_days.trim() : "120";
    const weighting = typeof req.query.weighting === "string"
      ? req.query.weighting.trim() : "equal";
    res.json(await getIndustryHypesAndDrains(benchmarkCode, periodDays, weighting));
  } catch (err) {
    console.error("[analysis/industry-hypes-and-drains] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/industry-attribution/member-indices
//     ?industry_id=BANKS&benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
//   Returns MemberIndexAttributionResponse: one row per member index with
//   code_sec_shared_weight + benchmark_sec_shared_weight for the given
//   (industry_id, benchmark_code, date). Drives the per-industry bar charts.
router.get("/industry-attribution/member-indices", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim() : "";
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim() : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getMemberIndexAttribution(industryId, benchmarkCode, date || null));
  } catch (err) {
    console.error("[analysis/industry-attribution/member-indices] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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
router.get("/industry-etf-contribution/etf-price", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.industry_ids === "string" ? req.query.industry_ids : "";
    const industryIds = raw.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
    if (industryIds.length === 0) {
      res.status(400).json({ error: "Missing 'industry_ids' parameter (comma-separated)" });
      return;
    }
    res.json(await getIndustryEtfPriceSeries(industryIds));
  } catch (err) {
    console.error("[analysis/industry-etf-contribution/etf-price] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-etf-contribution/etf-bars", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim() : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
    const date = rawDate || null;
    res.json(await getIndustryEtfContributionBars(industryId, date));
  } catch (err) {
    console.error("[analysis/industry-etf-contribution/etf-bars] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
