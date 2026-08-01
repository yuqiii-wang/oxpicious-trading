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
  listCapitalFlowIndustries,
  getCapitalFlowBenchmarks,
  getCapitalFlowCharts,
  listCapitalFlowThemes,
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

// ---- Capital Flow (Industry × Broad-Market Benchmark) --------------------
//   GET /api/analysis/capital-flow/industries
//     Returns all industries with rows in analysis.capital_flow, with latest
//     pure/observed popularity aggregated across benchmarks.
//
//   GET /api/analysis/capital-flow/themes
//     Returns the L1 sector → L2 industry tree (SectorNode[]) for the
//     ThemeSelector. Only industries present in analysis.capital_flow are
//     included, classified via stats.sec_classification.
//
//   GET /api/analysis/capital-flow/benchmarks?industry_id=AI
//     Returns per-benchmark breakdown for one industry.
//
//   GET /api/analysis/capital-flow/chart?industry_id=AI&benchmark_codes=000300,000852
//     Returns the per-date time series for one industry × a SET of benchmark
//     codes. `benchmark_codes` is a comma-separated list; only those benchmarks
//     are fetched (the frontend defaults to 000300 + 000852 so it loads just
//     the two plots it shows). Returns an array of CapitalFlowChartResponse,
//     one per requested code, in the requested order.
router.get("/capital-flow/industries", async (_req: Request, res: Response) => {
  try {
    res.json(await listCapitalFlowIndustries());
  } catch (err) {
    console.error("[analysis/capital-flow/industries] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/capital-flow/themes", async (_req: Request, res: Response) => {
  try {
    res.json(await listCapitalFlowThemes());
  } catch (err) {
    console.error("[analysis/capital-flow/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/capital-flow/benchmarks", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    res.json(await getCapitalFlowBenchmarks(industryId));
  } catch (err) {
    console.error("[analysis/capital-flow/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/capital-flow/chart", async (req: Request, res: Response) => {
  try {
    const industryId = typeof req.query.industry_id === "string"
      ? req.query.industry_id.trim()
      : "";
    if (!industryId) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    // benchmark_codes is a comma-separated list (e.g. "000300,000852").
    // For backward compatibility, a single benchmark_code param is also
    // accepted and treated as a one-element list.
    const rawCodes = typeof req.query.benchmark_codes === "string"
      ? req.query.benchmark_codes
      : (typeof req.query.benchmark_code === "string" ? req.query.benchmark_code : "");
    const codes = rawCodes
      .split(",")
      .map((c) => c.trim())
      .filter((c) => c.length > 0);
    if (codes.length === 0) {
      res.status(400).json({ error: "Missing 'benchmark_codes' parameter" });
      return;
    }
    res.json(await getCapitalFlowCharts(industryId, codes));
  } catch (err) {
    console.error("[analysis/capital-flow/chart] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
