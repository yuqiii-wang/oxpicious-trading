/**
 * Valuation routes (/valuation/*, /pe/*, /dividends/deliveries).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listValuationCodes,
  getPeChart,
  listValuationThemes,
  listValuationStrategyThemes,
  listPeStreaks,
  listDividendDeliveries,
} from "../../services/analysis/index.js";
import { parseSecType, parseCode, parseExchange } from "./_shared.js";
import { wrapAsync, HttpError } from "../../lib/http.js";

const router = Router();

// ---- PE & Dividend Yield (valuation) analytics
//   analysis.pe / analysis.dividends   — daily raw pe / trailing-12m
//                                        dividend_yield (split 2026-09)
//
//   Close price is NOT stored in the analysis tables
//   (they live in stats); the chart endpoint JOINs stats live at request time.
//
//   The /valuation/* routes below are the SHARED universe endpoints — the
//   code universe is the UNION of analysis.pe and analysis.dividends, and
//   BOTH the PE page and the Dividends page (their shared SecCodesPageShell)
//   consume them, so they are named for the universe rather than for one
//   page (2026-09-29; formerly the pe-named /pe/codes|themes|strategy-themes).
//   The PE-only chart / streaks reads stay under /pe/*.
//
//   GET /api/analysis/valuation/codes?sec_type=index
//     Returns ValuationCodesResponse: list of codes with first/last date,
//     n_dates, and latest pe + dividend_yield snapshot.
//   GET /api/analysis/valuation/themes?sec_type=index
//     Returns the L1 sector → L2 industry → items tree for SecClassificationNav,
//     filtered to codes that have rows in analysis.pe / analysis.dividends.
//   GET /api/analysis/valuation/strategy-themes?sec_type=index
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//
//   GET /api/analysis/pe/chart?sec_type=index&code=000300
//     Returns PeChartResponse: daily (date, close, pe,
//     dividend_yield) rows for one security. Close + pe are read live from
//     stats so the UI always shows the freshest source values.
//   GET /api/analysis/pe/streaks?sec_type=index&code=000300
//     Returns PeStreaksResponse: band-BREAK excursion streaks of the
//     code's pe series (analysis.pe_pct_streaks, side derived from the
//     end month's band), flat for ALL (period, pct_type) combos.
router.get("/valuation/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listValuationCodes(
    parseSecType(req),
    undefined, undefined, undefined, undefined,
    parseExchange(req),
  ));
}));

router.get("/valuation/themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listValuationThemes(parseSecType(req), parseExchange(req)));
}));

router.get("/valuation/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listValuationStrategyThemes(parseSecType(req), parseExchange(req)));
}));

router.get("/pe/chart", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await getPeChart(code, parseSecType(req)));
}));

//   GET /api/analysis/pe/streaks?sec_type=index&code=000300
//     Returns PeStreaksResponse: the band-BREAK excursion
//     streaks of the code's pe series (from analysis.pe_pct_streaks,
//     side derived at query time from the end month's band), shipped
//     flat for ALL (period, pct_type) combos — the client filters by
//     its nested selection.
router.get("/pe/streaks", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await listPeStreaks(code, parseSecType(req)));
}));

// ---- Dividend Deliveries (rolling 5y / 1y-stride audit table)
//   analysis.dividend_deliveries — per-(sec_type, code, window_end_date)
//   rolling 5-year delivery audit (yield / eps / same-month-last-year
//   stability), built by analysis.pe_and_dividends --metrics deliveries.
//   Serves the Dividends page's table under the baseline code-trend
//   chart; the estimation curve itself (analysis.dividend_est) rides
//   /api/sec-baseline's etf-margin join.
//
//   GET /api/analysis/dividends/deliveries?sec_type=etf&code=161010
//     Returns DividendDeliveriesResponse: the code's window rows,
//     newest first. Non-payers / index resolve to an empty list.
router.get("/dividends/deliveries", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await listDividendDeliveries(code, parseSecType(req)));
}));

export default router;
