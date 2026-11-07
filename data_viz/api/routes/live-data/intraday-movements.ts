/**
 * GET /intraday-movements, /intraday-movements/benchmarks,
 * /intraday-movements/dates and /intraday-movements/prev-day-ohlc — the
 * Market Movements tab's per-tick % change data.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getIntradayMovements,
  listIntradayMovementsBenchmarks,
  listIntradayMovementsDates,
  getIntradayMovementsPrevDayOhlc,
} from "../../services/analysis/index.js";

const router = Router();

// ---- Intraday Movements — per-5-min-tick % change vs prev day close for
//      the benchmark + ALL industries (shaded areas) + member indices.
//      Drives the "Market Movements" tab on the Live Data page.
//      Data is read from live.sec_alloc_live_attribution (populated by
//      python -m live.sec_alloc_live_attribution); industry identity
//      (stats.sec_classification) and aggregates are joined/computed at
//      query time.
//
//   GET /api/live-data/intraday-movements
//     ?benchmark_code=000922&date=YYYY-MM-DD
//     Returns IntradayMovementsResponse: benchmark % change per tick +
//     all industries' % change per tick (for shades + middle bar chart) +
//     member indices' % change per (code, tick) (for bottom bar chart).
//     `date` optional → latest available for the benchmark.
//   GET /api/live-data/intraday-movements/benchmarks
//     Returns the list of benchmark codes that appear in
//     stats.cross_stats / live.sec_alloc_live_attribution
//     (broad-market benchmarks first).
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

//   GET /api/live-data/intraday-movements/dates
//     ?benchmark_code=000300
//     Returns the distinct dates available for the benchmark (raw intraday
//     bars UNION live tick rows), newest first — drives the date selector.
router.get("/intraday-movements/dates", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string" ? req.query.benchmark_code : "";
    const dates = await listIntradayMovementsDates(benchmarkCode);
    res.json({ benchmark_code: benchmarkCode, dates });
  } catch (err) {
    console.error("[live-data/intraday-movements/dates] error:", err);
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

export default router;
