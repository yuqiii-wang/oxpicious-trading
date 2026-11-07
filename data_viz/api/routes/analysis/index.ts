/**
 * Analysis Commons API routes.
 *
 *  GET /api/analysis/mov-ave-spread/codes?sec_type=etf
 *    Returns the list of asset codes (ETF or index) that have analysis rows,
 *    with first/last date, n_dates, and the latest snapshot of all 9 gap
 *    values. `sec_type` must be 'etf' or 'index'.
 *
 *  GET /api/analysis/mov-ave-spread/chart?sec_type=etf&code=510050
 *    Returns all 9 pair time series for one asset (drives the 3×3 grid of
 *    small charts).
 *
 *  GET /api/analysis/mov-ave-spread/themes?sec_type=etf
 *    Returns the L1 sector → L2 industry → items tree for the ThemeSelector,
 *    filtered to codes that have rows in analysis.mov_ave_spreads_detail.
 */

// Split verbatim out of routes/analysis.ts into per-topic subfiles in this
// directory; this file composes the sub-routers in the original section order.

import { Router } from "express";
import runRouter from "./run.js";
import movAveSpreadRouter from "./mov-ave-spread.js";
import perfAttrRouter from "./perf-attr.js";
import marketBetaRouter from "./market-beta.js";
import industryCorrRouter from "./industry-corr.js";
import industryAttributionRouter from "./industry-attribution.js";
import valuationRouter from "./valuation.js";
import crowdednessRouter from "./crowdedness.js";
import hotStockRouter from "./hot-stock.js";
import marginTrendsRouter from "./margin-trends.js";

const router = Router();

router.use(runRouter);
router.use(movAveSpreadRouter);
router.use(perfAttrRouter);
router.use(marketBetaRouter);
router.use(industryCorrRouter);
router.use(industryAttributionRouter);
router.use(valuationRouter);
router.use(crowdednessRouter);
router.use(hotStockRouter);
router.use(marginTrendsRouter);

// Re-exported for parity with the old routes/analysis.ts module surface.
export { INDUSTRY_CORR_RUN_TAG, INDUSTRY_CORR_OFFSET_RUN_TAG } from "./industry-corr.js";

export default router;
