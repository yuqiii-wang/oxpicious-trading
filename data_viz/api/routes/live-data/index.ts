// The route-catalog doc comment below is extracted verbatim from
// routes/live-data.ts — the routes themselves live in this directory's
// subfiles; this file composes them in the original section order.
/**
 * Live Data API routes.
 *
 * Three endpoints:
 *   GET /api/live-data/dates?type=index|stock
 *     → { type, dates: ["2026-07-31", ...] }  (descending — most recent first)
 *
 *   GET /api/live-data/themes?type=index|stock
 *     → L1 sector → L2 industry tree (SectorNode[]) restricted to codes with
 *       at least one intraday bar.
 *
 *   GET /api/live-data/combined?type=index|stock&date=&sector=&industry=
 *       &exchange=&code=&page=&page_size=
 *     → paginated list of codes with their intraday bars for the requested
 *       date (defaults to the latest available date).
 *
 * ETF ('type=etf') is intentionally NOT supported here — no
 * stats.etf_intraday_5min table exists yet. The frontend renders an empty
 * placeholder for the ETF tab until that table is added.
 */

import { Router } from "express";
import snapshotRouter from "./snapshot.js";
import intradayMovementsRouter from "./intraday-movements.js";
import secAllocLiveRouter from "./sec-alloc-live.js";
import tradingSignalsRouter from "./trading-signals.js";

const router = Router();

router.use(snapshotRouter);
router.use(intradayMovementsRouter);
router.use(secAllocLiveRouter);
router.use(tradingSignalsRouter);

export default router;
