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
  getForecastTable,
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
  getIndustryCorrOffsets,
  listIndustryCorrOffsetBenchmarks,
  listIndustryCorrOffsetIndustries,
  getIndustryBenchmarkAttribution,
  listIndustryAttributionBenchmarks,
  getBenchmarkPriceChart,
  getIndustryAttributionPriceSeries,
  getAllIndustriesAttribution,
  getMemberIndexAttribution,
  getIndustryEtfPriceSeries,
  getIndustryEtfContributionBars,
  getIndustryHypesAndDrains,
  listPeAndDividendCodes,
  getPeAndDividendChart,
  listPeAndDividendThemes,
  listPeAndDividendStrategyThemes,
  listPeAndDividendStats,
  listPeAndDividendStreaks,
  listMarginTrendThemes,
  listMarginTrendStrategyThemes,
  getMarginIndustrySeries,
  getMarginTrends,
  getRecurringCyclesChart,
  getRecurringCyclesSpectrum,
  listRecurringCyclesThemes,
  listRecurringCyclesStrategyThemes,
  getFuturesExt,
} from "../services/analysis/index.js";
import {
  runPythonModule,
  getPythonProcessStatus,
} from "../services/py-runner.service.js";
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

// ---------------------------------------------------------------------------
//  Run one analysis main for ONE security (UI per-security build button).
//
//  POST /api/analysis/run-analysis   body: { module, sec_type, code }
//    Spawns `python -m analyze.<module> --sec-type <st> --code <code>` via
//    the shared WSL py-runner and WAITS for it to exit. Deduped by
//    process-id-tag — a second click while a run is in flight resolves
//    immediately with `already_running: true`.
//
//  GET /api/analysis/run-analysis/status?process_id_tag=a,b
//    → { status: { [tag]: boolean } } — running-state of the tags, so a
//    page refresh can put the button straight back into its spinning
//    state until the remote process exits.
// ---------------------------------------------------------------------------

/** Analysis mains that support single-security recomputation (--code). */
const RUNNABLE_ANALYSIS_MODULES = new Set([
  "mov_ave_spread",
  "recurring_cycles",
  "pe_and_dividends",
]);

router.post("/run-analysis", async (req: Request, res: Response) => {
  try {
    const module = typeof req.body?.module === "string" ? req.body.module : "";
    const secType = typeof req.body?.sec_type === "string" ? req.body.sec_type : "";
    const code = typeof req.body?.code === "string" ? req.body.code : "";
    if (!RUNNABLE_ANALYSIS_MODULES.has(module)) {
      res.status(400).json({
        success: false,
        stderr_tail: `Unknown analysis module '${module}' (supported: ${[...RUNNABLE_ANALYSIS_MODULES].join(", ")})`,
      });
      return;
    }
    if (!code || !secType) {
      res.status(400).json({ success: false, stderr_tail: "Missing 'sec_type' or 'code'" });
      return;
    }
    const tag = `analysis-run:${module}:${secType}:${code}`;
    console.log(
      `[analysis/run-analysis] python -m analyze.${module} --sec-type ${secType} --code ${code}`,
    );
    const result = await runPythonModule(
      `analyze.${module}`,
      ["--sec-type", secType, "--code", code],
      { processIdTag: tag },
    );
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: tag,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/run-analysis] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

router.get("/run-analysis/status", (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.process_id_tag === "string" ? req.query.process_id_tag : "";
    const tags = raw.split(",").map((t) => t.trim()).filter((t) => t.length > 0);
    res.json({ status: getPythonProcessStatus(tags) });
  } catch (err) {
    console.error("[analysis/run-analysis/status] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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

// ---- Forecast buckets table (2nd plot beneath the spread chart) ----
// GET /api/analysis/mov-ave-spread/forecast?sec_type=etf&code=510050&kind=mov_rsi
//   kind ∈ {mov_rsi, mov_std, mov_gap, px_vol} — returns the code's bucket
//   rows (bucket config incl. cooldown_days + is_market_hyped + excess cols
//   for mov_std / mean_t + mean_z for px_vol, read from
//   forecast_results.config) joined 1:1 with their
//   analysis_forecasts.forecast_results columns. ALL stat_months are
//   returned; optional `month=YYYY-MM-DD` narrows to stat_months >= month.
//   The response's `months` lists every available stat_month.
router.get("/mov-ave-spread/forecast", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const kind = typeof req.query.kind === "string" ? req.query.kind : undefined;
    const month = typeof req.query.month === "string" ? req.query.month : null;
    res.json(await getForecastTable(parseSecType(req), code, kind, month));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/forecast] error:", err);
    res.status(400).json({ error: String(err) });
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
// includes codes that have rows in stats.cross_stats (sec_type='index').
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
//   NO per-code aggregation table — the per-index data is queried directly
//   from stats.index_basic_stats JOIN stats.sec_classification at request
//   time; the precomputed mean/var overlay comes from stats.industry_basic_stats
//   (renamed from analysis.industry_sentiments 2026-08-24, built by
//   builds.industry). The frontend rebases each member index to 100 at the
//   start of the displayed (zoom) window (scale-invariant comparison across
//   indices with different absolute price levels).
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
router.get("/industry-sentiments/themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listIndustrySentimentsThemes(exchange));
  } catch (err) {
    console.error("[analysis/industry-sentiments/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/analysis/industry-sentiments/strategy-themes */
router.get("/industry-sentiments/strategy-themes", async (req: Request, res: Response) => {
  try {
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listIndustrySentimentsStrategyThemes(exchange));
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

// ---- Industry Correlations (windowed pairwise correlation between
//      industries' MA curves of mean_close — drives the Correlation chart
//      on the IndustrySentiments page when 2+ industries are selected).
//   GET /api/analysis/industry-correlations?industry_ids=BANKS,AI&pool_size=all
//     Returns IndustryCorrelationsResponse: one row per (start_date, pair)
//     for every lexicographic (a<b) pair from the user-selected industry_ids
//     set, with corr_ma20_20d / corr_ma60_60d / corr_ma255_255d (Pearson
//     correlation of the two industries' MA-W curves over the W trading
//     days starting on start_date; window starts every `interval` trading
//     days). The frontend renders one line per pair, with a window toggle
//     (20d/60d/255d).
//   POST /api/analysis/industry-correlations/run   body:
//     { industry_ids?: string[], codes?: string[] }
//     Spawns `python -m analyze.industry_sentiments.corr --industry ...
//     --code ...` (filtered mode: recompute + upsert ALL windows for the
//     pairs among the given industries; codes are resolved to industry_ids
//     Python-side) via the shared WSL py-runner and WAITS for it to exit.
//     Deduped by a fixed process-id-tag, so a second click while a refresh
//     is in flight resolves with already_running=true. Running-state is
//     polled via GET /api/analysis/run-analysis/status (generic tags
//     endpoint) with INDUSTRY_CORR_RUN_TAG.
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

/** Fixed process-id-tag for UI-triggered industry-corr refresh runs —
 *  one refresh at a time globally (the status endpoint + button spinner
 *  poll this tag; must match the tag built client-side). */
export const INDUSTRY_CORR_RUN_TAG = "analysis-run:industry_corr";

/** Parse a string[] body field (industry_ids / codes) — accepts arrays or
 *  comma-separated strings, drops empties. */
function parseBodyList(v: unknown): string[] {
  if (Array.isArray(v)) {
    return v.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  }
  if (typeof v === "string" && v.length > 0) {
    return v.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
  }
  return [];
}

router.post("/industry-correlations/run", async (req: Request, res: Response) => {
  try {
    const industryIds = parseBodyList(req.body?.industry_ids);
    const codes = parseBodyList(req.body?.codes);
    if (industryIds.length + codes.length === 0) {
      res.status(400).json({
        success: false,
        stderr_tail: "Missing 'industry_ids' or 'codes' (at least one entry)",
      });
      return;
    }
    const args: string[] = [];
    if (industryIds.length > 0) args.push("--industry", industryIds.join(","));
    if (codes.length > 0) args.push("--code", codes.join(","));
    console.log(
      `[analysis/industry-correlations/run] python -m analyze.industry_sentiments.corr ${args.join(" ")}`,
    );
    const result = await runPythonModule(
      "analyze.industry_sentiments.corr",
      args,
      { processIdTag: INDUSTRY_CORR_RUN_TAG },
    );
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: INDUSTRY_CORR_RUN_TAG,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/industry-correlations/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

// ---- Industry Correlations by Benchmark Offset (composite analysis —
//      opposite industry correlations; drives the Composites page).
//   GET /api/analysis/industry-corr-offsets?industry_ids=BANKS,AI
//         &pool_size=all&benchmark=000300
//     Returns IndustryCorrOffsetsResponse: one audit row per
//     (start_date, lexicographic pair) for every pair from the
//     user-selected industry_ids set, with the RAW overall correlation,
//     the benchmark-offset sub / add recomputed-price correlations and the
//     derived opposite score (1 - sub)/2, at 20d/60d/255d windows.
//   GET /api/analysis/industry-corr-offsets/benchmarks
//     Returns the distinct benchmark_code values materialized in
//     analysis_composites.industry_corr_benchmark_offsets (benchmark
//     dropdown; empty list until the analysis has been run).
//   POST /api/analysis/industry-corr-offsets/run   body:
//     { industry_ids?: string[], codes?: string[], benchmark?: string }
//     Spawns `python -m analyze.analysis_composites --industry ...
//     --code ... --benchmark ...` (filtered mode: recompute + upsert ALL
//     windows for the pairs among the given industries) via the shared
//     py-runner and WAITS for it to exit. Deduped by a fixed
//     process-id-tag, so a second click while a refresh is in flight
//     resolves with already_running=true. Running-state is polled via
//     GET /api/analysis/run-analysis/status with
//     INDUSTRY_CORR_OFFSET_RUN_TAG.
router.get("/industry-corr-offsets", async (req: Request, res: Response) => {
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
    const benchmark = typeof req.query.benchmark === "string"
      ? req.query.benchmark.trim()
      : "000300";
    res.json(await getIndustryCorrOffsets(industryIds, poolSize, benchmark));
  } catch (err) {
    console.error("[analysis/industry-corr-offsets] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-corr-offsets/benchmarks", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustryCorrOffsetBenchmarks());
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/analysis/industry-corr-offsets/industries — selectable industry
// list (distinct type='index' industries + has_rows flag).
router.get("/industry-corr-offsets/industries", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustryCorrOffsetIndustries());
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/industries] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Fixed process-id-tag for UI-triggered offset-corr refresh runs — one
 *  refresh at a time globally (must match the tag built client-side). */
export const INDUSTRY_CORR_OFFSET_RUN_TAG = "analysis-run:industry_corr_offsets";

router.post("/industry-corr-offsets/run", async (req: Request, res: Response) => {
  try {
    const industryIds = parseBodyList(req.body?.industry_ids);
    const codes = parseBodyList(req.body?.codes);
    const benchmarkRaw = typeof req.body?.benchmark === "string"
      ? req.body.benchmark.trim()
      : "";
    if (industryIds.length + codes.length === 0) {
      res.status(400).json({
        success: false,
        stderr_tail: "Missing 'industry_ids' or 'codes' (at least one entry)",
      });
      return;
    }
    const args: string[] = [];
    if (industryIds.length > 0) args.push("--industry", industryIds.join(","));
    if (codes.length > 0) args.push("--code", codes.join(","));
    if (benchmarkRaw) args.push("--benchmark", benchmarkRaw);
    console.log(
      `[analysis/industry-corr-offsets/run] python -m analyze.analysis_composites ${args.join(" ")}`,
    );
    const result = await runPythonModule(
      "analyze.analysis_composites",
      args,
      { processIdTag: INDUSTRY_CORR_OFFSET_RUN_TAG },
    );
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: INDUSTRY_CORR_OFFSET_RUN_TAG,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

// ---- Industry-level Benchmark Attribution (aggregated
//      stats.cross_stats pair rows per industry_id). Drives the "Benchmark
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

// ---- PE & Dividend Yield (per-(sec_type, code, date) valuation analytics)
//   analysis.pe_and_dividends          — daily pe_ma20 + dividend_yield
//   analysis.pe_and_dividend_stats     — monthly 5y rolling stats snapshot
//
//   Close price and raw PE ratio are NOT stored in analysis.pe_and_dividends
//   (they live in stats); the chart endpoint JOINs stats live at request time.
//
//   GET /api/analysis/pe-and-dividend/codes?sec_type=index
//     Returns PeAndDividendCodesResponse: list of codes with first/last date,
//     n_dates, and latest pe_ma20 + dividend_yield snapshot.
//   GET /api/analysis/pe-and-dividend/chart?sec_type=index&code=000300
//     Returns PeAndDividendChartResponse: daily (date, close, pe, pe_ma20,
//     dividend_yield) rows for one security. Close + pe are read live from
//     stats so the UI always shows the freshest source values.
//   GET /api/analysis/pe-and-dividend/themes?sec_type=index
//     Returns the L1 sector → L2 industry → items tree for SecClassificationNav,
//     filtered to codes that have rows in analysis.pe_and_dividends.
//   GET /api/analysis/pe-and-dividend/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/pe-and-dividend/stats?sec_type=index&code=000300
//     Returns PeAndDividendStatsResponse: ALL monthly 5y rolling stats
//     snapshots for one code (most recent first). is_active marks the latest.
//   GET /api/analysis/pe-and-dividend/streaks?sec_type=index&code=000300
//     Returns PeAndDividendStreaksResponse: band-BREAK excursion streaks of
//     the code's pe_ma20 / dividend_yield series
//     (analysis.pe_and_dividend_pct_streaks, side derived from the end
//     month's band), flat for ALL (metric, period, pct_type) combos.
router.get("/pe-and-dividend/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listPeAndDividendCodes(
      parseSecType(req),
      undefined, undefined, undefined, undefined,
      parseExchange(req),
    ));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/pe-and-dividend/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getPeAndDividendChart(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/pe-and-dividend/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listPeAndDividendThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/pe-and-dividend/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listPeAndDividendStrategyThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/pe-and-dividend/stats", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await listPeAndDividendStats(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/stats] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/pe-and-dividend/streaks?sec_type=index&code=000300
//     Returns PeAndDividendStreaksResponse: the band-BREAK excursion
//     streaks of the code's pe_ma20 / dividend_yield series (from
//     analysis.pe_and_dividend_pct_streaks, side derived at query time
//     from the end month's band), shipped flat for ALL (metric, period,
//     pct_type) combos — the client filters by its nested selection.
router.get("/pe-and-dividend/streaks", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await listPeAndDividendStreaks(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/pe-and-dividend/streaks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Margin Trends (single-industry RONGZI margin flows)
//   analysis.margin_index_series (TABLE) — weighted-avg constituent-stock
//                                          margin per (index_code, date),
//                                          built by Python vectorization
//   analysis.margin_changes             — trend episodes (shade overlay)
//
//   GET /api/analysis/margin-trends/themes
//     L1 sector → L2 industry tree (industries WITH margin data).
//   GET /api/analysis/margin-trends/strategy-themes
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/margin-trends/industry-series?industry_id=BANKS&attribution=index
//     Per-(security, date) margin series for one industry + attribution.
//     'index' reads the margin_index_series TABLE; 'etf' reads
//     stats.etf_liquidity_margin for the industry's ETFs.
router.get("/margin-trends/themes", async (req: Request, res: Response) => {
  try {
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    res.json(await listMarginTrendThemes(attribution));
  } catch (err) {
    console.error("[analysis/margin-trends/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/margin-trends/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMarginTrendStrategyThemes());
  } catch (err) {
    console.error("[analysis/margin-trends/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/margin-trends/industry-series", async (req: Request, res: Response) => {
  try {
    const industryId = (req.query.industry_id as string | undefined) ?? "";
    if (!industryId.trim()) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    res.json(await getMarginIndustrySeries(industryId, attribution));
  } catch (err) {
    console.error("[analysis/margin-trends/industry-series] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/margin-trends/trends?industry_id=BANKS&attribution=index
//     Sustained UP/DOWN trend episode date ranges for the securities in one
//     industry + attribution. Used by the 1st plot to render light shade
//     (markArea) over each trend window.
router.get("/margin-trends/trends", async (req: Request, res: Response) => {
  try {
    const industryId = (req.query.industry_id as string | undefined) ?? "";
    if (!industryId.trim()) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    res.json(await getMarginTrends(industryId, attribution));
  } catch (err) {
    console.error("[analysis/margin-trends/trends] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Recurring Cycles (recurring rise/drop periodicity of close prices)
//   analysis.recurring_cycles — per-(sec_type, code, last_date, range_days)
//   recurring rise/drop periodicity: every integer day period d (2..N/2)
//   audited for RECURRENCE in the time domain (extrema evidence × ACF
//   coherence, amplitude-gated); headline period_days = argmax of strength
//   (0 = no recurring period). Currently populated for sec_type='index' only.
//
//   The table is 55 GB (per-row spectra arrays) — no page-load query may
//   scan it. The navigation trees resolve "codes with data" from the
//   analysis.recurring_cycles_codes registry (maintained by the Python
//   populator), and every recurring_cycles read below is code-filtered so
//   the PK index drives it — the UI must pass `code` in the filter.
//
//   GET /api/analysis/recurring-cycles/chart?sec_type=index&code=000300
//     Returns RecurringCyclesChartResponse: per-(last_date, range_days)
//     period_days + strength rows for one security.
//   GET /api/analysis/recurring-cycles/themes?sec_type=index
//     Returns the L1 sector → L2 industry → items tree for SecClassificationNav,
//     restricted to codes registered in analysis.recurring_cycles_codes.
//   GET /api/analysis/recurring-cycles/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
router.get("/recurring-cycles/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getRecurringCyclesChart(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/recurring-cycles/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/recurring-cycles/spectrum?sec_type=index&code=000300&last_date=2026-01-08
//     Returns RecurringCyclesSpectrumResponse: the per-day recurring
//     periodicity factors (amplitude / count / strength spectra, day-aligned:
//     element j = day j+2) for ONE (code, last_date) across ALL range_days
//     windows. last_date is optional — when omitted, defaults to the latest
//     available date for that code (so the page has an initial spectrum
//     before the user clicks a date on the top index price plot). Drives the
//     per-date bar charts below the top price plot on the Recurring Cycles
//     page.
router.get("/recurring-cycles/spectrum", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const rawDate = typeof req.query.last_date === "string" ? req.query.last_date.trim() : "";
    const lastDate = rawDate || null;
    res.json(await getRecurringCyclesSpectrum(code, parseSecType(req), lastDate));
  } catch (err) {
    console.error("[analysis/recurring-cycles/spectrum] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/recurring-cycles/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listRecurringCyclesThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/recurring-cycles/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/recurring-cycles/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listRecurringCyclesStrategyThemes(parseSecType(req), parseExchange(req)));
  } catch (err) {
    console.error("[analysis/recurring-cycles/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Futures Analysis — basis + correlation per (date, code) for a product
//   analysis.futures_ext JOIN stats.futures_identity on product_code
//
//   GET /api/analysis/futures/ext?product=IF
//     Returns FuturesExtResponse: gapByCodeDate (for 1st plot tooltip),
//     corrByCodeDate (for 2nd plot correlation chart), and flat rows.
router.get("/futures/ext", async (req: Request, res: Response) => {
  try {
    const product = (req.query.product as string | undefined) ?? "";
    if (!product.trim()) {
      res.status(400).json({ error: "Missing 'product' parameter" });
      return;
    }
    const ext = await getFuturesExt(product);
    // Serialize Maps to plain objects for JSON
    const gapObj: Record<string, Record<string, number | null>> = {};
    ext.gapByCodeDate.forEach((dateMap, code) => {
      gapObj[code] = {};
      dateMap.forEach((val, date) => { gapObj[code][date] = val; });
    });
    const corrObj: Record<string, Record<string, number | null>> = {};
    ext.corrByCodeDate.forEach((dateMap, code) => {
      corrObj[code] = {};
      dateMap.forEach((val, date) => { corrObj[code][date] = val; });
    });
    res.json({
      product: ext.product,
      gapByCodeDate: gapObj,
      corrByCodeDate: corrObj,
      rows: ext.rows,
    });
  } catch (err) {
    console.error("[analysis/futures/ext] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
