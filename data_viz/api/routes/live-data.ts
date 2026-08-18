/**
 * Live Data API routes.
 *
 * Three endpoints:
 *   GET /api/live-data/dates?type=index|stock
 *     → { type, dates: ["2026-07-31", ...] }  (descending — most recent first)
 *
 *   GET /api/live-data/themes?type=index|stock
 *     → L1 sector → L2 industry tree (SectorNode[]) restricted to codes with
 *       at least one intraday bar.
 *
 *   GET /api/live-data/combined?type=index|stock&date=&sector=&industry=
 *       &exchange=&code=&page=&page_size=
 *     → paginated list of codes with their intraday bars for the requested
 *       date (defaults to the latest available date).
 *
 * ETF ('type=etf') is intentionally NOT supported here — no
 * stats.etf_intraday_5min table exists yet. The frontend renders an empty
 * placeholder for the ETF tab until that table is added.
 */
import { Router, type Request, type Response } from "express";
import {
  listLiveDataDates,
  listLiveDataThemes,
  listStrategyThemes,
  getLiveDataCombined,
  type LiveDataSecType,
} from "../services/live-data.service.js";
import {
  getIntradayMovements,
  listIntradayMovementsBenchmarks,
  getIntradayMovementsPrevDayOhlc,
} from "../services/analysis/index.js";
import { runPythonModule } from "../services/py-runner.service.js";
import { getSecAllocLiveAttribution } from "../services/sec-alloc-live-attribution.service.js";

const router = Router();

function parseSecType(v: unknown): LiveDataSecType {
  return v === "stock" ? "stock" : "index";
}

/** GET /api/live-data/dates?type=index */
router.get("/dates", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    res.json(await listLiveDataDates(secType));
  } catch (err) {
    console.error("[live-data/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/themes?type=index */
router.get("/themes", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listLiveDataThemes(secType, exchange));
  } catch (err) {
    console.error("[live-data/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/strategy-themes?type=index — parallel strategy → theme tree */
router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listStrategyThemes(secType, exchange));
  } catch (err) {
    console.error("[live-data/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/combined?type=index&date=&sector=&industry=... */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      strategy: typeof req.query.strategy === "string" ? req.query.strategy : undefined,
      theme: typeof req.query.theme === "string" ? req.query.theme : undefined,
      code: typeof req.query.code === "string" ? req.query.code : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
      date: typeof req.query.date === "string" ? req.query.date : undefined,
      page:
        typeof req.query.page === "string"
          ? parseInt(req.query.page, 10)
          : undefined,
      page_size:
        typeof req.query.page_size === "string"
          ? parseInt(req.query.page_size, 10)
          : undefined,
    };
    res.json(await getLiveDataCombined(secType, query));
  } catch (err) {
    console.error("[live-data/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Intraday Movements — per-5-min-tick % change vs prev day close for
//      the benchmark + ALL industries (shaded areas) + member indices.
//      Drives the "Market Movements" tab on the Live Data page.
//      Data is pre-computed by analyze.intraday_industry_sentiments into
//      analysis.intraday_industry_market_movements (parent) +
//      analysis.intraday_index_market_movements (child).
//
//   GET /api/live-data/intraday-movements
//     ?benchmark_code=000922&date=YYYY-MM-DD
//     Returns IntradayMovementsResponse: benchmark % change per tick +
//     all industries' % change per tick (for shades + middle bar chart) +
//     member indices' % change per (code, tick) (for bottom bar chart).
//     `date` optional → latest available for the benchmark.
//   GET /api/live-data/intraday-movements/benchmarks
//     Returns the list of benchmark codes that appear in
//     analysis.sec_alloc_perf_attribution (broad-market benchmarks first).
//   GET /api/live-data/intraday-movements/prev-day-ohlc
//     ?benchmark_code=000922&date=YYYY-MM-DD
//     Returns PrevDayOhlcResponse: raw prev-trading-day OHLC of the benchmark
//     + every member index (with industry_id) — drives the single prev-day
//     OHLC bar before the 09:30 tick on the top plot.
router.get("/intraday-movements/benchmarks", async (_req: Request, res: Response) => {
  try {
    const benchmarks = await listIntradayMovementsBenchmarks();
    res.json({ benchmarks });
  } catch (err) {
    console.error("[live-data/intraday-movements/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/intraday-movements/prev-day-ohlc", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getIntradayMovementsPrevDayOhlc(benchmarkCode, date || null));
  } catch (err) {
    console.error("[live-data/intraday-movements/prev-day-ohlc] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/intraday-movements", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getIntradayMovements(benchmarkCode, date || null));
  } catch (err) {
    console.error("[live-data/intraday-movements] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Sec-Alloc Live Attribution data — per-industry weighted/equal
//      aggregates at one 5-min tick from the live schema tables.
//      weighted_available drives the UI "By Trading Amt" disable state.
//      GET /api/live-data/sec-alloc-live/attribution
router.get("/sec-alloc-live/attribution", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    const time = typeof req.query.time === "string" ? req.query.time.trim() : "";
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    if (!benchmarkCode || !time) {
      res.status(400).json({ error: "Missing 'benchmark_code' or 'time' parameter" });
      return;
    }
    res.json(await getSecAllocLiveAttribution(benchmarkCode, date || null, time));
  } catch (err) {
    console.error("[live-data/sec-alloc-live/attribution] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Sec-Alloc Live Attribution pipeline trigger.
//      The Market Movements page fires this POST before each 5-min
//      auto-refresh so the underlying tables stay fresh during trading
//      hours without manual runs. The module is incremental:
//        • heavy prev-date ref (live.sec_alloc_live_prev_ref) is built
//          ONCE per date and skipped when already present;
//        • light 5-min ticks (live.sec_alloc_live_attribution) are
//          appended for new bars only.
//      An in-flight guard prevents overlapping spawns when the 5-min
//      interval fires while a previous run is still executing.
let secAllocLiveRunInFlight = false;

/** POST /api/live-data/sec-alloc-live/run */
router.post("/sec-alloc-live/run", async (_req: Request, res: Response) => {
  if (secAllocLiveRunInFlight) {
    res.json({ success: true, skipped_in_flight: true });
    return;
  }
  secAllocLiveRunInFlight = true;
  try {
    const result = await runPythonModule("live.sec_alloc_live_attribution", []);
    res.json({
      success: result.success,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[live-data/sec-alloc-live/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  } finally {
    secAllocLiveRunInFlight = false;
  }
});

export default router;
