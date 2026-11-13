/**
 * Options API routes (all venues — SZSE / SSE ETF options and CFFEX index
 * options; the /api/szse-options URL prefix is kept for client stability).
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, enumParam, boolParam } from "../lib/http.js";
import {
  listUnderlyings,
  getOptionsCombined,
  getOptionsWalls,
  getOptionsWallsDominant,
  getEtfOhlcv,
  getOptionsSkewnessSeries,
  getOptionsOiStats,
  getOptionsBands,
  getOptionsIvSkew,
  getOptionsVolIndex,
  getOptionsVolIndexCorr,
  getOptionsDates,
  getOptionsSnapshotRows,
  getOptionsSnapshotAxes,
  getOptionsSkewDaily,
  getOptionsOiDaily,
  getOptionsOiCells,
  getOptionsExpiryMeta,
  getOptionsMoneynessSeries,
} from "../services/market/options.service.js";
import { SKEW_TYPES } from "../../shared/types.js";

const router = Router();

/** ?fresh=1 — Refresh-button bypass for the version-keyed aggregate cache. */
function wantsFresh(req: Request): boolean {
  return boolParam(req, "fresh") === true;
}

router.get("/underlyings", wrapAsync(async (req: Request, res: Response) => {
  const targetType = strParam(req, "target_type");
  const exchange = strParam(req, "exchange");
  res.json(await listUnderlyings(targetType, exchange, wantsFresh(req)));
}));

router.get("/combined", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    underlying: strParam(req, "underlying"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
    target_type: strParam(req, "target_type"),
    exchange: strParam(req, "exchange"),
  };
  res.json(await getOptionsCombined(query));
}));

router.get("/walls", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    underlying: strParam(req, "underlying"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
  };
  res.json(await getOptionsWalls(query));
}));

/** Per-plot: the DOMINANT zone wall per (date, side) — the skew panel's
 *  wall shades without the per-expiry bulk (the Options Trend cohort
 *  chart keeps /walls). */
router.get("/walls-dominant", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    underlying: strParam(req, "underlying"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
  };
  res.json(await getOptionsWallsDominant(query));
}));

router.get("/etf-ohlcv", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const data = await getEtfOhlcv(
    code,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
    strParam(req, "target_type"),
  );
  res.json(data);
}));

router.get("/skewness-series", wrapAsync(async (req: Request, res: Response) => {
  const underlying = strParam(req, "underlying", { required: true });
  const skewType = enumParam(req, "skew_type", SKEW_TYPES, { default: "oi_moneyness" });
  const data = await getOptionsSkewnessSeries(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
    skewType,
  );
  res.json(data);
}));

router.get("/oi-stats", wrapAsync(async (req: Request, res: Response) => {
  const underlying = strParam(req, "underlying", { required: true });
  const data = await getOptionsOiStats(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
  );
  res.json(data);
}));

router.get("/bands", wrapAsync(async (req: Request, res: Response) => {
  const underlying = strParam(req, "underlying", { required: true });
  const data = await getOptionsBands(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
  );
  res.json(data);
}));

router.get("/iv-skew", wrapAsync(async (req: Request, res: Response) => {
  const underlying = strParam(req, "underlying", { required: true });
  const data = await getOptionsIvSkew(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
  );
  res.json(data);
}));

/** Shared query-param parser for the per-plot endpoints. */
function parsePlotQuery(req: Request) {
  return {
    underlying: strParam(req, "underlying"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
    target_type: strParam(req, "target_type"),
    exchange: strParam(req, "exchange"),
  };
}

/** Per-plot: distinct trading dates + counters (date picker, caption). */
router.get("/dates", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsDates(parsePlotQuery(req), wantsFresh(req)));
}));

/** Per-plot: quote rows for one or more specific dates
 *  (?dates=YYYY-MM-DD,YYYY-MM-DD,… — index-narrow per date, no history scan). */
router.get("/snapshot-rows", wrapAsync(async (req: Request, res: Response) => {
  const rawDates = strParam(req, "dates");
  const dates = rawDates
    ? rawDates.split(",").map((s) => s.trim()).filter(Boolean)
    : [];
  res.json(await getOptionsSnapshotRows(parsePlotQuery(req), dates));
}));

/** Per-plot: history-wide strike/expiry-month axes (wall panel) — cached
 *  per underlying/venue, never varies with the selected date. */
router.get("/snapshot-axes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsSnapshotAxes(parsePlotQuery(req), wantsFresh(req)));
}));

/** Per-plot: per (date × real expiry) smile anchors (ATM + 25Δ/10Δ wings)
 *  for the Smile Chronology panel's Today AND History views — replaces the
 *  panel's in-browser recompute of the full-history /combined blob. */
router.get("/skew-daily", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsSkewDaily(parsePlotQuery(req), wantsFresh(req)));
}));

/** Per-plot: per (date × expiry) call/put OI + spot (trend charts). */
router.get("/oi-daily", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsOiDaily(parsePlotQuery(req), wantsFresh(req)));
}));

/** Per-plot: per (date × expiry × strike) OI cells for the selected
 *  expiry cohorts (?expiries=YYYY-MM-DD,YYYY-MM-DD,…). */
router.get("/oi-cells", wrapAsync(async (req: Request, res: Response) => {
  const rawExpiries = strParam(req, "expiries");
  const expiries = rawExpiries
    ? rawExpiries.split(",").map((s) => s.trim()).filter(Boolean)
    : [];
  res.json(await getOptionsOiCells(parsePlotQuery(req), expiries));
}));

/** Per-plot: per-contract final OI expiry markers (cohort totals are
 *  derived client-side from /oi-daily). */
router.get("/expiry-meta", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsExpiryMeta(parsePlotQuery(req), wantsFresh(req)));
}));

/** Per-plot: server-computed oi_moneyness skew series (spot + groups). */
router.get("/oi-moneyness-series", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getOptionsMoneynessSeries(parsePlotQuery(req), wantsFresh(req)));
}));

router.get("/vol-index", wrapAsync(async (req: Request, res: Response) => {
  // underlying optional — omitting it returns every underlying's series
  // (cross-asset comparison chart).
  const underlying = strParam(req, "underlying");
  const data = await getOptionsVolIndex(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
  );
  res.json(data);
}));

router.get("/vol-index-corr", wrapAsync(async (req: Request, res: Response) => {
  // underlying optional — omitting it returns every underlying's series.
  const underlying = strParam(req, "underlying");
  const data = await getOptionsVolIndexCorr(
    underlying,
    strParam(req, "start_date"),
    strParam(req, "end_date"),
  );
  res.json(data);
}));

export default router;
