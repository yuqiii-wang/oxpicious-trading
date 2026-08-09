/**
 * Analysis Commons API routes.
 *
 *  GET /api/analysis/mov-ave-spread/codes?sec_type=etf
 *    Returns the list of asset codes (ETF or index) that have analysis rows,
 *    with first/last date, n_dates, and the latest snapshot of all 9 gap
 *    values. `sec_type` must be 'etf' or 'index'.
 *
 *  GET /api/analysis/mov-ave-spread/chart?sec_type=etf&code=510050
 *    Returns all 9 pair time series for one asset (drives the 3×3 grid of
 *    small charts).
 *
 *  GET /api/analysis/mov-ave-spread/themes?sec_type=etf
 *    Returns the L1 sector → L2 industry → items tree for the ThemeSelector,
 *    filtered to codes that have rows in analysis.mov_ave_spreads_detail.
 */
import { Router, type Request, type Response } from "express";
import {
  listMovAveSpreadCodes,
  getMovAveSpreadChart,
  listMovAveSpreadThemes,
  listMovAveSpreadStrategyThemes,
  listPerfAttrCodes,
  getPerfAttrChart,
  getPerfAttrAttribution,
  listPerfAttrThemes,
  listPerfAttrStrategyThemes,
  getIndustrySentimentsChart,
  getIndustrySentimentsChartByCode,
  listIndustrySentimentsThemes,
  listIndustrySentimentsStrategyThemes,
  getIndustryCorrelations,
  getIndustryBenchmarkAttribution,
  listIndustryAttributionBenchmarks,
  getBenchmarkPriceChart,
  getIndustryAttributionPriceSeries,
  getAllIndustriesAttribution,
  getMemberIndexAttribution,
  getIndustryEtfPriceSeries,
  getIndustryEtfContributionBars,
} from "../services/analysis/index.js";
import type { PerfAttrSecType } from "../../shared/types.js";

const router = Router();

/** Parse the `sec_type` query param — required on every endpoint. */
function parseSecType(req: Request): string | undefined {
  const v = req.query.sec_type;
  return typeof v === "string" ? v : undefined;
}

/** Parse the `code` query param — required on chart / summary endpoints. */
function parseCode(req: Request): string | null {
  const v = req.query.code;
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Parse the optional `exchange` query param (PRIMARY/SS/SZ/BJ/HK/OVERSEAS). */
function parseExchange(req: Request): string | null {
  const v = req.query.exchange;
  return typeof v === "string" && v.length > 0 ? v : null;
}

router.get("/mov-ave-spread/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listMovAveSpreadCodes(
      parseSecType(req),
      undefined, undefined, undefined, undefined,
      parseExchange(req),
    ));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/mov-ave-spread/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMovAveSpreadChart(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- MA-Spread themes tree (L1 sector → L2 industry → items)
// Mirrors /api/analysis/perf-attr/themes but only includes codes that have
// rows in analysis.mov_ave_spreads_detail for the requested sec_type. Used
// by the ThemeSelector on the MA-Spread page.
router.get("/mov-ave-spread/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMovAveSpreadThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/mov-ave-spread/strategy-themes?sec_type=etf */
router.get("/mov-ave-spread/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMovAveSpreadStrategyThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Performance Attribution (ETF/Index × Index) -------------------------
const VALID_PERF_ATTR_SEC_TYPES = new Set(["etf", "index"]);

function parsePerfAttrSecType(req: Request): PerfAttrSecType {
  const raw = typeof req.query.sec_type === "string" ? req.query.sec_type : "";
  return VALID_PERF_ATTR_SEC_TYPES.has(raw) ? (raw as PerfAttrSecType) : "etf";
}

router.get("/perf-attr/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listPerfAttrCodes(parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Performance Attribution: themes tree (L1 sector → L2 industry → items)
// Mirrors /api/etf-margin/themes and /api/index-baseline/themes but only
// includes codes that have rows in analysis.sec_alloc_perf_attribution.
router.get("/perf-attr/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listPerfAttrThemes(parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/perf-attr/strategy-themes?sec_type=etf */
router.get("/perf-attr/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listPerfAttrStrategyThemes(parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/perf-attr/attribution", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    // Optional `date` query param — when present, returns the attribution for
    // that specific date instead of the latest. Used by the expanded-plots
    // date click handler to refresh only the Fluctuation Attribution chart.
    const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
    const date = rawDate || null;
    res.json(await getPerfAttrAttribution(code, parsePerfAttrSecType(req), date));
  } catch (err) {
    console.error("[analysis/perf-attr/attribution] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/perf-attr/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    res.json(await getPerfAttrChart(code, benchmarkCode, parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry Sentiments (member index values, rebased to 100 client-side)
//   NO analysis.industry_sentiments table — the data is queried directly from
//   stats.index_basic_stats JOIN stats.sec_classification at request time.
//   The frontend rebases each member index to 100 at the start of the
//   displayed (zoom) window (scale-invariant comparison across indices with
//   different absolute price levels).
//
//   GET /api/analysis/industry-sentiments/themes
//     Returns the L1 sector → L2 industry tree (SectorNode[]) built directly
//     from stats.sec_classification (type='index'). Each industry's chip
//     count = number of member indices in that industry.
//
//   GET /api/analysis/industry-sentiments/chart?industry_id=BANKS
//     Returns per-index close time series for ONE industry. One entry per
//     member index, each with its raw daily close series. The frontend
//     rebases each to 100 at the start of the visible (zoom) window.
router.get("/industry-sentiments/themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustrySentimentsThemes());
  } catch (err) {
    console.error("[analysis/industry-sentiments/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/industry-sentiments/strategy-themes */
router.get("/industry-sentiments/strategy-themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustrySentimentsStrategyThemes());
  } catch (err) {
    console.error("[analysis/industry-sentiments/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-sentiments/chart", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    res.json(await getIndustrySentimentsChart(industryId));
  } catch (err) {
    console.error("[analysis/industry-sentiments/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/industry-sentiments/chart-by-code?code=931696
 *  Returns chart data (close series + stock_num) for a single index code.
 *  Used when an L3 index chip is clicked under a strategy/theme —
 *  strategy-primary indices may lack an industry_id classification. */
router.get("/industry-sentiments/chart-by-code", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getIndustrySentimentsChartByCode(code));
  } catch (err) {
    console.error("[analysis/industry-sentiments/chart-by-code] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry Correlations (pairwise rolling correlation between
//      industries' mean_price series — drives the Correlation chart on
//      the IndustrySentiments page when 2+ industries are selected).
//   GET /api/analysis/industry-correlations?industry_ids=BANKS,AI&pool_size=all
//     Returns IndustryCorrelationsResponse: one row per (date, pair) for
//     every lexicographic (a<b) pair from the user-selected industry_ids
//     set, with corr_5d / corr_20d / corr_60d / corr_255d. The frontend
//     renders one line per pair, with a window toggle (5d/20d/60d/255d).
router.get("/industry-correlations", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.industry_ids === "string"
      ? req.query.industry_ids
      : "";
    const industryIds = raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    if (industryIds.length < 2) {
      res.status(400).json({
        error: "Need at least 2 industry_ids (comma-separated)",
      });
      return;
    }
    const poolSize = typeof req.query.pool_size === "string"
      ? req.query.pool_size.trim()
      : "all";
    res.json(await getIndustryCorrelations(industryIds, poolSize));
  } catch (err) {
    console.error("[analysis/industry-correlations] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry-level Benchmark Attribution (aggregated
//      sec_alloc_perf_attribution per industry_id). Drives the "Benchmark
//      Attribution" view on the IndustrySentiments page — the toggle that
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
//      Industry Sentiments page.
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
//      "ETF Contribution" view on the Industry Sentiments page.
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
