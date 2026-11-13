/**
 * Performance Attribution routes (/perf-attr/*).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listPerfAttrCodes,
  getPerfAttrChart,
  getPerfAttrAttribution,
  listPerfAttrThemes,
  listPerfAttrStrategyThemes,
} from "../../services/analysis/index.js";
import type { PerfAttrSecType } from "../../../shared/types.js";
import { parseCode } from "./_shared.js";
import { wrapAsync, strParam, HttpError } from "../../lib/http.js";

const router = Router();

// ---- Performance Attribution (ETF/Index × Index) -------------------------
const VALID_PERF_ATTR_SEC_TYPES = new Set(["etf", "index"]);

function parsePerfAttrSecType(req: Request): PerfAttrSecType {
  const raw = typeof req.query.sec_type === "string" ? req.query.sec_type : "";
  return VALID_PERF_ATTR_SEC_TYPES.has(raw) ? (raw as PerfAttrSecType) : "etf";
}

router.get("/perf-attr/codes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listPerfAttrCodes(parsePerfAttrSecType(req)));
}));

// ---- Performance Attribution: themes tree (L1 sector → L2 industry → items)
// Mirrors /api/etf-margin/themes and /api/index/themes but only
// includes codes that have rows in stats.cross_stats (sec_type='index').
router.get("/perf-attr/themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listPerfAttrThemes(parsePerfAttrSecType(req)));
}));

/** GET /api/analysis/perf-attr/strategy-themes?sec_type=etf */
router.get("/perf-attr/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listPerfAttrStrategyThemes(parsePerfAttrSecType(req)));
}));

router.get("/perf-attr/attribution", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  // Optional `date` query param — when present, returns the attribution for
  // that specific date instead of the latest. Used by the expanded-plots
  // date click handler to refresh only the Fluctuation Attribution chart.
  const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
  const date = rawDate || null;
  res.json(await getPerfAttrAttribution(code, parsePerfAttrSecType(req), date));
}));

router.get("/perf-attr/chart", wrapAsync(async (req: Request, res: Response) => {
  const code = parseCode(req);
  if (!code) throw HttpError.missingParam("code");
  const benchmarkCode = strParam(req, "benchmark_code", { required: true });
  res.json(await getPerfAttrChart(code, benchmarkCode, parsePerfAttrSecType(req)));
}));

export default router;
