/**
 * Market Crowdedness routes (/market-crowdedness/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listMarketCrowdednessCodes,
  getMarketCrowdednessChart,
  listMarketCrowdednessThemes,
  listMarketCrowdednessStrategyThemes,
} from "../../services/analysis/index.js";
import { parseSecType, parseCode, parseExchange } from "./_shared.js";

const router = Router();

// ---- Market Crowdedness (analysis.market_crowdedness)
//   Per-(code, sec_type, date) crowdedness curve for indices and
//   composite industries, built by analysis.market_crowdedness.
//   crowdedness_score = 0.5*concentration + 0.5*flow_change from the
//   pool's dynamic top-10-by-trading-amount hot set. The nav/codes
//   universe is the FULL classification (stats.sec_classification
//   type='index' + stats.industry_identity entities) — deliberately NOT
//   restricted to codes with rows (index side = the 10 study indices;
//   other codes plot close + amount only).
//
//   GET /api/analysis/market-crowdedness/codes?sec_type=index&exchange=SS
//     Returns MarketCrowdednessCodesResponse: the flat (code, name)
//     universe for the page's codes shell (index | industry).
//   GET /api/analysis/market-crowdedness/themes?sec_type=index
//     L1 sector → L2 industry → items tree for SecClassificationNav.
//   GET /api/analysis/market-crowdedness/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/market-crowdedness/chart?sec_type=index&code=000001
//     Returns MarketCrowdednessChartResponse: daily (date, close,
//     trading_amount, crowdedness_score, concentration, flow_change,
//     top_stocks) rows for one security — metric fields NULL on dates
//     with no defined crowdedness row.
router.get("/market-crowdedness/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listMarketCrowdednessCodes(
      parseSecType(req),
      parseExchange(req),
    ));
  } catch (err) {
    console.error("[analysis/market-crowdedness/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/market-crowdedness/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMarketCrowdednessThemes(
      parseSecType(req),
      parseExchange(req),
    ));
  } catch (err) {
    console.error("[analysis/market-crowdedness/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/market-crowdedness/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMarketCrowdednessStrategyThemes(
      parseSecType(req),
      parseExchange(req),
    ));
  } catch (err) {
    console.error("[analysis/market-crowdedness/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/market-crowdedness/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMarketCrowdednessChart(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/market-crowdedness/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
