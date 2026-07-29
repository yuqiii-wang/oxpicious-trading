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
    res.json(await getPerfAttrAttribution(code, parsePerfAttrSecType(req)));
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

export default router;
