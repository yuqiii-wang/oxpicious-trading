/**
 * Hot Stock Momentum routes (/hot-stock-momentum/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listHotStockMomentumCodes,
  getHotStockMomentumChart,
  listHotStockMomentumThemes,
  listHotStockMomentumStrategyThemes,
} from "../../services/analysis/index.js";
import { parseSecType, parseCode, parseExchange } from "./_shared.js";
import { wrapAsync, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Hot Stock Momentum (analysis.hot_stock_momentum)
//   Per-(code, sec_type, date) hot-stock momentum curve for indices and
//   composite industries, built by analysis.hot_stock_momentum.
//   IMPLEMENTATION PLACEHOLDER (2026-10-31): the pipeline run() is empty,
//   so /chart returns empty rows until it lands. The nav/codes universe
//   is the FULL classification (stats.sec_classification type='index' +
//   stats.industry_identity entities) — deliberately NOT restricted to
//   codes with rows, so the Commons page is browsable meanwhile.
//
//   GET /api/analysis/hot-stock-momentum/codes?sec_type=index&exchange=SS
//     Returns HotStockMomentumCodesResponse: the flat (code, name)
//     universe for the page's codes shell (index | industry).
//   GET /api/analysis/hot-stock-momentum/themes?sec_type=index
//     L1 sector → L2 industry → items tree for SecClassificationNav.
//   GET /api/analysis/hot-stock-momentum/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/hot-stock-momentum/chart?sec_type=index&code=000001
//     Returns HotStockMomentumChartResponse: daily (date, momentum) rows
//     for one security — empty until the pipeline is implemented.
router.get("/hot-stock-momentum/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listHotStockMomentumCodes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/hot-stock-momentum/themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listHotStockMomentumThemes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/hot-stock-momentum/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listHotStockMomentumStrategyThemes(
    parseSecType(req),
    parseExchange(req),
  ));
}));

router.get("/hot-stock-momentum/chart", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await getHotStockMomentumChart(code, parseSecType(req)));
}));

export default router;
