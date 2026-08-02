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
 */
import { Router, type Request, type Response } from "express";
import {
  listMovAveSpreadCodes,
  getMovAveSpreadChart,
  listPerfAttrCodes,
  getPerfAttrChart,
  getPerfAttrAttribution,
  listPerfAttrThemes,
  getIndustrySentimentsChart,
  listIndustrySentimentsThemes,
  getIndustryCorrelations,
} from "../services/analysis.service.js";
import type { PerfAttrSecType } from "../../shared/types.js";

const router = Router();

/** Parse the `sec_type` query param — required on every endpoint. */
function parseSecType(req: Request): string | undefined {
  const v = req.query.sec_type;
  return typeof v === "string" ? v : undefined;
}

/** Parse the `code` query param — required on chart / summary endpoints. */
function parseCode(req: Request): string | null {
  const v = req.query.code;
  return typeof v === "string" && v.length > 0 ? v : null;
}

router.get("/mov-ave-spread/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listMovAveSpreadCodes(parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/mov-ave-spread/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    res.json(await getMovAveSpreadChart(code, parseSecType(req)));
  } catch (err) {
    console.error("[analysis/mov-ave-spread/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Performance Attribution (ETF/Index × Index) -------------------------
const VALID_PERF_ATTR_SEC_TYPES = new Set(["etf", "index"]);

function parsePerfAttrSecType(req: Request): PerfAttrSecType {
  const raw = typeof req.query.sec_type === "string" ? req.query.sec_type : "";
  return VALID_PERF_ATTR_SEC_TYPES.has(raw) ? (raw as PerfAttrSecType) : "etf";
}

router.get("/perf-attr/codes", async (req: Request, res: Response) => {
  try {
    res.json(await listPerfAttrCodes(parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/codes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Performance Attribution: themes tree (L1 sector → L2 industry → items)
// Mirrors /api/etf-margin/themes and /api/index-baseline/themes but only
// includes codes that have rows in analysis.sec_alloc_perf_attribution.
router.get("/perf-attr/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listPerfAttrThemes(parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/perf-attr/attribution", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    // Optional `date` query param — when present, returns the attribution for
    // that specific date instead of the latest. Used by the expanded-plots
    // date click handler to refresh only the Fluctuation Attribution chart.
    const rawDate = typeof req.query.date === "string" ? req.query.date.trim() : "";
    const date = rawDate || null;
    res.json(await getPerfAttrAttribution(code, parsePerfAttrSecType(req), date));
  } catch (err) {
    console.error("[analysis/perf-attr/attribution] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/perf-attr/chart", async (req: Request, res: Response) => {
  try {
    const code = parseCode(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    res.json(await getPerfAttrChart(code, benchmarkCode, parsePerfAttrSecType(req)));
  } catch (err) {
    console.error("[analysis/perf-attr/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry Sentiments (member index values, rebased to 100 client-side)
//   NO analysis.industry_sentiments table — the data is queried directly from
//   stats.index_basic_stats JOIN stats.sec_classification at request time.
//   The frontend rebases each member index to 100 at the start of the
//   displayed (zoom) window (scale-invariant comparison across indices with
//   different absolute price levels).
//
//   GET /api/analysis/industry-sentiments/themes
//     Returns the L1 sector → L2 industry tree (SectorNode[]) built directly
//     from stats.sec_classification (type='index'). Each industry's chip
//     count = number of member indices in that industry.
//
//   GET /api/analysis/industry-sentiments/chart?industry_id=BANKS
//     Returns per-index close time series for ONE industry. One entry per
//     member index, each with its raw daily close series. The frontend
//     rebases each to 100 at the start of the visible (zoom) window.
router.get("/industry-sentiments/themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustrySentimentsThemes());
  } catch (err) {
    console.error("[analysis/industry-sentiments/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-sentiments/chart", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    res.json(await getIndustrySentimentsChart(industryId));
  } catch (err) {
    console.error("[analysis/industry-sentiments/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Industry Correlations (pairwise rolling correlation between
//      industries' mean_rebased series — drives the Correlation chart on
//      the IndustrySentiments page when 2+ industries are selected).
//   GET /api/analysis/industry-correlations?industry_ids=BANKS,AI&pool_size=all
//     Returns IndustryCorrelationsResponse: one row per (date, pair) for
//     every lexicographic (a<b) pair from the user-selected industry_ids
//     set, with corr_5d / corr_20d / corr_60d / corr_255d. The frontend
//     renders one line per pair, with a window toggle (5d/20d/60d/255d).
router.get("/industry-correlations", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.industry_ids === "string"
      ? req.query.industry_ids
      : "";
    const industryIds = raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    if (industryIds.length < 2) {
      res.status(400).json({
        error: "Need at least 2 industry_ids (comma-separated)",
      });
      return;
    }
    const poolSize = typeof req.query.pool_size === "string"
      ? req.query.pool_size.trim()
      : "all";
    res.json(await getIndustryCorrelations(industryIds, poolSize));
  } catch (err) {
    console.error("[analysis/industry-correlations] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
