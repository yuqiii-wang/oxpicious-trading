/**
 * Sec Composition API routes.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, HttpError } from "../lib/http.js";
import { getSecComposition, getQuarterlyComposition, getLinkedEtfs, getLinkedFeeders, getSimilarIndices, getIndustryWeightSeries } from "../services/sec/sec-composition.service.js";

const router = Router();

/** GET /api/sec-composition?code=159001[&date=2026-06-30]
 *  Without `date`: latest snapshot. With `date`: latest snapshot within the
 *  calendar quarter containing the date (the "by season" lookup). */
router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  const date = strParam(req, "date");
  res.json(await getSecComposition(code, date));
}));

/** GET /api/sec-composition/quarterly?code=159673
 *  Per-quarter industry-aggregated composition (latest snapshot within each
 *  quarter; quarters without a snapshot are absent). Falls back to the
 *  tracking index when the ETF has no snapshots. */
router.get("/quarterly", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getQuarterlyComposition(code));
}));

/** GET /api/sec-composition/industry-weight-series?code=159673&industry_id=BANKS
 *  ONE industry's weight in the security's composition across ALL snapshot
 *  dates (roughly monthly; denser than the quarterly view). Falls back to the
 *  tracking index when the ETF has no snapshots. Used by the ETF Holdings
 *  page's Industry-changes row drill-down. */
router.get("/industry-weight-series", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code");
  const industryId = strParam(req, "industry_id");
  if (!code || !industryId) {
    throw new HttpError(
      400,
      "MISSING_PARAM",
      "Missing 'code' or 'industry_id' parameter",
    );
  }
  res.json(await getIndustryWeightSeries(code, industryId));
}));

/** GET /api/sec-composition/linked-etfs?code=000300
 *  Returns the ETFs tracking the given index (parent_index_code = code),
 *  enriched with latest close + n_days from stats.v_etf_margin, per-ETF
 *  feeder-fund counts from stats.sec_etf_links, and the index's display
 *  name — the payload behind the unified Index→ETF→feeder table. */
router.get("/linked-etfs", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getLinkedEtfs(code));
}));

/** GET /api/sec-composition/linked-feeders?code=510880
 *  Returns the ETF联接基金 feeder funds whose target (master) ETF is the
 *  given code, from stats.sec_etf_links. ETF-level mirror of /linked-etfs. */
router.get("/linked-feeders", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getLinkedFeeders(code));
}));

/** GET /api/sec-composition/similar-indices?code=000300
 *  Returns the top-3 similar indices by mutual shared composition weight for
 *  the given index, from stats.sec_similars (sec_type='index', latest snapshot <= today). */
router.get("/similar-indices", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code", { required: true });
  res.json(await getSimilarIndices(code));
}));

export default router;
