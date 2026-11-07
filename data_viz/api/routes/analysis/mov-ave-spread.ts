/**
 * MA-Spread panel routes (/mov-ave-spread/*) + /market-regimes.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listMovAveSpreadCodes,
  getMovAveSpreadChart,
  getMovAveSpreadChartPairs,
  getMovAveSpreadChartEma,
  getMovAveSpreadChartShared,
  getMovAveSpreadChartExtras,
  getMarketRegimeSpans,
  listMovAveSpreadThemes,
  listMovAveSpreadStrategyThemes,
} from "../../services/analysis/index.js";
import { parseSecType, parseCode, parseExchange } from "./_shared.js";

const router = Router();

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

// ---- MA-Spread chart BLOCKS (per-table async loads) ----
// The panel fires all three in parallel and renders as soon as the REQUIRED
// pairs block lands; ema populates the EMA chip grid and shared feeds the
// tooltip metrics when they arrive (both best-effort).
// GET /api/analysis/mov-ave-spread/chart/pairs?sec_type=stock&code=600256
router.get("/mov-ave-spread/chart/pairs", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMovAveSpreadChartPairs(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/chart/pairs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/analysis/mov-ave-spread/chart/ema?sec_type=stock&code=600256
router.get("/mov-ave-spread/chart/ema", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMovAveSpreadChartEma(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/chart/ema] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/analysis/mov-ave-spread/chart/shared?sec_type=stock&code=600256
router.get("/mov-ave-spread/chart/shared", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMovAveSpreadChartShared(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/chart/shared] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- MA-Spread on-demand metric groups ----
// GET /api/analysis/mov-ave-spread/extras?sec_type=etf&code=510050&metrics=amt,ohlc
//   metrics = comma-separated subset of amt | ohlc | rfest | rfaudit |
//   streaks | pxvol | gaps | rsiranks. Only the requested groups are queried
//   and present in the response. The panel fetches a group the first time one
//   of its control buttons is picked (Amt/MA chips, Roof/Floor Trendlines +
//   Est levels + the rebound audit, High/Low Streaks, Px-Vol States, Top-10%
//   OHLC Days, the RSI Trends Top-% select) so the default page load carries
//   just the pair series.
router.get("/mov-ave-spread/extras", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const metrics = typeof req.query.metrics === "string" ? req.query.metrics : "";
    res.json(await getMovAveSpreadChartExtras(code, parseSecType(req), metrics));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/extras] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Market-regime SPANS (stats.market_regime_spans table) ----
// GET /api/analysis/market-regimes?sec_type=etf&code=510050
//   One (sec_type, code)'s contiguous same-regime spans (calm/hot/
//   panic/quiet), grouped per regime — the shading source for the
//   shared CodeTrendChart's Regimes toggle and the MA-Spread panel's
//   regime chip group. Pure row read — spans are materialized by
//   builds.market_regimes alongside the daily registry. Replaces the
//   retired /market-hypes episodes endpoint (stats.mov_ave_market_hypes
//   — dropped).
router.get("/market-regimes", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMarketRegimeSpans(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/market-regimes] error:", err);
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

export default router;
