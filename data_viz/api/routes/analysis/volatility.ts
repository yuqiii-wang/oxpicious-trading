/**
 * Index Vol Stats routes (/volatility/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listVolatilityCodes,
  getVolatilitySeries,
} from "../../services/analysis/index.js";
import { parseCode, parseSecType } from "./_shared.js";
import { wrapAsync, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Volatility (analysis.volatility)
//   Per-(sec_type, code, date) realized-volatility panel, built by
//   analysis.volatility (structured for all security sec_types — the
//   endpoints accept etf/index/stock; the RUN SCOPE is index only for
//   now, the 10 study indices): close-to-close historical vol across
//   5/10/20/60/120/252-row windows, the RiskMetrics EWMA vol (λ=0.94),
//   the Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang 20-row
//   OHLC estimators, the overnight-vs-intraday split, Wilder
//   ATR(14)/NATR(14), Bollinger band width, vol-of-vol, the short/long
//   vol ratio, trailing-252 percentile ranks + shifted-1 z-scores, and
//   the 5-minute realized vol with bipower jump ratio where the
//   sec_type's stats.*_intraday_5min table has bars (~90-day
//   retention). All *vol* fields percent annualized.
//
//   GET /api/analysis/volatility/codes[?sec_type=index]
//     Returns VolatilityCodesResponse: the codes with vol rows for the
//     sec_type with their latest identity display names — the
//     SecCodesPageShell codes fetcher.
//   GET /api/analysis/volatility/series?code=000001[&sec_type=index]
//     Returns VolatilitySeriesResponse: one security's FULL daily
//     series (the UI slices via dataZoom) — early rows NULL through
//     estimator warm-up, the rv leg NULL outside the intraday-bar
//     retention window.
router.get("/volatility/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listVolatilityCodes(parseSecType(req)));
}));

router.get("/volatility/series", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await getVolatilitySeries(code, parseSecType(req)));
}));

export default router;
