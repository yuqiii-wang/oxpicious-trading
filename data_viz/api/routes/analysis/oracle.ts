/**
 * Oracle routes (/oracle/*) — the hindsight ORACLE tier.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listOracleCodes,
  getOracleSeries,
} from "../../services/analysis/index.js";
import { parseCode, parseSecType } from "./_shared.js";
import { wrapAsync, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Oracle (analysis.oracle_signals)
//   Per-(sec_type, code, date) hindsight oracle pivot actions, built
//   by analysis.oracle_signals (DECOUPLED from the analysis_signals
//   rule tier; structured for all security sec_types, RUN SCOPE index
//   only for now — the 10 study indices): v3 grammar — local extremes
//   of the 5-bar window [date-2, date+2] scanned with a 1-day stride,
//   ONE action per 5-consecutive-row window (the first candidate
//   claims the unit; a more extreme same-side candidate within it
//   upgrades the placement): buy at a local min, sell at a local max,
//   each row carrying the next opposite action as its hindsight swing
//   target (NULL at the frontier). The trail ends 2 bars before the
//   data frontier (the pivot confirmation window).
//
//   GET /api/analysis/oracle/codes[?sec_type=index]
//     Returns OracleCodesResponse: the codes with decisions for the
//     sec_type with their latest identity display names — the
//     SecCodesPageShell codes fetcher.
//   GET /api/analysis/oracle/series?code=000001[&sec_type=index]
//     Returns OracleSeriesResponse: one security's FULL action history
//     (the UI slices via dataZoom), each row carrying the action + the
//     5d window + the swing-target legs (the next opposite action's
//     date / offset / price) + the action date's raw close (the UI's
//     price overlay + marker anchor).
router.get("/oracle/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listOracleCodes(parseSecType(req)));
}));

router.get("/oracle/series", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  res.json(await getOracleSeries(code, parseSecType(req)));
}));

export default router;
