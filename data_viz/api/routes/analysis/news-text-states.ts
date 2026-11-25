/**
 * News Text States routes (/news-text-states/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getNewsTextStatesChart,
  listNewsTextDisagreement,
  listNewsTextStatesEvents,
  listNewsTextStatesRecentLevels,
  listNewsTextStatesScopes,
  listNewsTextStrongNegEvents,
} from "../../services/analysis/index.js";
import { wrapAsync } from "../../lib/http.js";

const router = Router();

// ---- News Text States (text.news_text_daily)
//   The daily news-text state panel built by analysis.news_text_daily:
//   per (date, scope) the lexicon-blended sentiment level with the ewm
//   halflife-3 (shock) / halflife-10 (persistent, validated contrarian
//   at the monthly tail) state lines, the abnormal-news-flow risk flag
//   (63d median/IQR — validated negative forward IC), sign-split article
//   counts, origin dispersion, and the kw_market tone cross-check;
//   industry-kind rows exist only on event days, sector-kind rows (kind
//   3) the same event-day aggregates rolled up the taxonomy to the L1
//   parent sector.
//
//   GET /api/analysis/news-text-states/scopes
//     Returns NewsTextStatesScopesResponse: the (scope_kind, scope_code)
//     selector source with row counts + date spans (kind labels decoded
//     through the 'news_scope_kind' label index).
//   GET /api/analysis/news-text-states/chart?scope_kind=0&scope_code=000001
//     Returns NewsTextStatesChartResponse: daily (date, close,
//     sent_level, sent_z, sent_ema3, sent_ema10, sent_delta, flow_af,
//     flow_pos/flow_neg, disp_origin, lex_tone_z, low_coverage) rows —
//     close read live from stats (SSE Composite for the market scopes,
//     the industry composite for kind 2, the member-industry mean for
//     kind 3); metric fields NULL on dates with no defined row. Default
//     scope = market_all.
//   GET /api/analysis/news-text-states/events?limit=40
//     Returns NewsTextStatesEventsResponse: the recent industry event
//     days (kind 2 rows, newest first).
//   GET /api/analysis/news-text-states/disagreement?window_days=120
//     Returns NewsTextStatesDisagreementResponse: daily lexicon-vs-LLM
//     disagreement counts over the corpus (the ambiguity flag).
//   GET /api/analysis/news-text-states/strong-neg-events?window_days=120&limit=20
//     Returns NewsTextStatesStrongNegEventsResponse: the recent
//     strong-negative industry event days (level <= -1.5 — the
//     validated reversal-event cut).
//   GET /api/analysis/news-text-states/recent-levels?window_days=20
//     Returns NewsTextStatesRecentLevelsResponse: the cross-section —
//     per industry (kind 2) and sector (kind 3) scope its LATEST
//     event-day reading within the window; scopes with no event day in
//     the window are absent.
router.get("/news-text-states/scopes", wrapAsync(async (_req: Request, res: Response) => {
  res.json(await listNewsTextStatesScopes());
}));

router.get("/news-text-states/chart", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getNewsTextStatesChart(
    typeof req.query.scope_kind === "string"
      ? req.query.scope_kind
      : undefined,
    typeof req.query.scope_code === "string"
      ? req.query.scope_code
      : undefined,
  ));
}));

router.get("/news-text-states/events", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsTextStatesEvents(
    typeof req.query.limit === "string" ? req.query.limit : undefined,
  ));
}));

router.get("/news-text-states/disagreement", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsTextDisagreement(
    typeof req.query.window_days === "string" ? req.query.window_days : undefined,
  ));
}));

router.get("/news-text-states/strong-neg-events", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsTextStrongNegEvents(
    typeof req.query.window_days === "string" ? req.query.window_days : undefined,
    typeof req.query.limit === "string" ? req.query.limit : undefined,
  ));
}));

router.get("/news-text-states/recent-levels", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsTextStatesRecentLevels(
    typeof req.query.window_days === "string" ? req.query.window_days : undefined,
  ));
}));

export default router;
