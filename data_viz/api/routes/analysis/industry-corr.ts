/**
 * Industry Correlations + Benchmark Offset correlation routes
 * (/industry-correlations*, /industry-corr-offsets*), including the fixed
 * process-id-tag constants for the two run endpoints.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getIndustryCorrelations,
  getIndustryCorrOffsets,
  listIndustryCorrOffsetBenchmarks,
  listIndustryCorrOffsetIndustries,
  invalidateIndustryCorrOffsetCoverage,
} from "../../services/analysis/index.js";
import { runPythonModule } from "../../services/py-runner.service.js";

const router = Router();

// ---- Industry Correlations (windowed pairwise correlation between
//      industries' MA curves of mean_close — drives the Correlation chart
//      on the MarketBeta page when 2+ industries are selected).
//   GET /api/analysis/industry-correlations?industry_ids=BANKS,AI&pool_size=all
//     Returns IndustryCorrelationsResponse: one row per (start_date, pair)
//     for every lexicographic (a<b) pair from the user-selected industry_ids
//     set, with corr_ma20_20d / corr_ma60_60d / corr_ma255_255d (Pearson
//     correlation of the two industries' MA-W curves over the W trading
//     days starting on start_date; window starts every `interval` trading
//     days). The frontend renders one line per pair, with a window toggle
//     (20d/60d/255d).
//   POST /api/analysis/industry-correlations/run   body:
//     { industry_ids?: string[], codes?: string[] }
//     Spawns `python -m analysis.market_beta.corr --industry ...
//     --code ...` (filtered mode: recompute + upsert ALL windows for the
//     pairs among the given industries; codes are resolved to industry_ids
//     Python-side) via the shared WSL py-runner and WAITS for it to exit.
//     Deduped by a fixed process-id-tag, so a second click while a refresh
//     is in flight resolves with already_running=true. Running-state is
//     polled via GET /api/analysis/run-analysis/status (generic tags
//     endpoint) with INDUSTRY_CORR_RUN_TAG.
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

/** Fixed process-id-tag for UI-triggered industry-corr refresh runs —
 *  one refresh at a time globally (the status endpoint + button spinner
 *  poll this tag; must match the tag built client-side). */
export const INDUSTRY_CORR_RUN_TAG = "analysis-run:industry_corr";

/** Parse a string[] body field (industry_ids / codes) — accepts arrays or
 *  comma-separated strings, drops empties. */
function parseBodyList(v: unknown): string[] {
  if (Array.isArray(v)) {
    return v.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  }
  if (typeof v === "string" && v.length > 0) {
    return v.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
  }
  return [];
}

router.post("/industry-correlations/run", async (req: Request, res: Response) => {
  try {
    const industryIds = parseBodyList(req.body?.industry_ids);
    const codes = parseBodyList(req.body?.codes);
    if (industryIds.length + codes.length === 0) {
      res.status(400).json({
        success: false,
        stderr_tail: "Missing 'industry_ids' or 'codes' (at least one entry)",
      });
      return;
    }
    const args: string[] = [];
    if (industryIds.length > 0) args.push("--industry", industryIds.join(","));
    if (codes.length > 0) args.push("--code", codes.join(","));
    console.log(
      `[analysis/industry-correlations/run] python -m analysis.market_beta.corr ${args.join(" ")}`,
    );
    const result = await runPythonModule(
      "analysis.market_beta.corr",
      args,
      { processIdTag: INDUSTRY_CORR_RUN_TAG },
    );
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: INDUSTRY_CORR_RUN_TAG,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/industry-correlations/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

// ---- Industry Correlations by Benchmark Offset (composite analysis —
//      opposite industry correlations; drives the Composites page).
//   GET /api/analysis/industry-corr-offsets?industry_ids=BANKS,AI
//         &pool_size=all&benchmark=000300
//     Returns IndustryCorrOffsetsResponse: one audit row per
//     (start_date, lexicographic pair) for every pair from the
//     user-selected industry_ids set, with the RAW overall correlation,
//     the benchmark-offset sub / add recomputed-price correlations and the
//     derived opposite score (1 - sub)/2, at 20d/60d/255d windows.
//   GET /api/analysis/industry-corr-offsets/benchmarks
//     Returns the distinct benchmark_code values materialized in
//     analysis_composites.industry_corr_benchmark_offsets (benchmark
//     dropdown; empty list until the analysis has been run).
//   POST /api/analysis/industry-corr-offsets/run   body:
//     { industry_ids?: string[], codes?: string[], benchmark?: string }
//     Spawns `python -m analysis.analysis_composites --industry ...
//     --code ... --benchmark ...` (filtered mode: recompute + upsert ALL
//     windows for the pairs among the given industries) via the shared
//     py-runner and WAITS for it to exit. Deduped by a fixed
//     process-id-tag, so a second click while a refresh is in flight
//     resolves with already_running=true. Running-state is polled via
//     GET /api/analysis/run-analysis/status with
//     INDUSTRY_CORR_OFFSET_RUN_TAG.
router.get("/industry-corr-offsets", async (req: Request, res: Response) => {
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
    const benchmark = typeof req.query.benchmark === "string"
      ? req.query.benchmark.trim()
      : "000300";
    res.json(await getIndustryCorrOffsets(industryIds, poolSize, benchmark));
  } catch (err) {
    console.error("[analysis/industry-corr-offsets] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/industry-corr-offsets/benchmarks", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustryCorrOffsetBenchmarks());
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/analysis/industry-corr-offsets/industries — selectable industry
// list (distinct type='index' industries + has_rows flag).
router.get("/industry-corr-offsets/industries", async (_req: Request, res: Response) => {
  try {
    res.json(await listIndustryCorrOffsetIndustries());
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/industries] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Fixed process-id-tag for UI-triggered offset-corr refresh runs — one
 *  refresh at a time globally (must match the tag built client-side). */
export const INDUSTRY_CORR_OFFSET_RUN_TAG = "analysis-run:industry_corr_offsets";

router.post("/industry-corr-offsets/run", async (req: Request, res: Response) => {
  try {
    const industryIds = parseBodyList(req.body?.industry_ids);
    const codes = parseBodyList(req.body?.codes);
    const benchmarkRaw = typeof req.body?.benchmark === "string"
      ? req.body.benchmark.trim()
      : "";
    if (industryIds.length + codes.length === 0) {
      res.status(400).json({
        success: false,
        stderr_tail: "Missing 'industry_ids' or 'codes' (at least one entry)",
      });
      return;
    }
    const args: string[] = [];
    if (industryIds.length > 0) args.push("--industry", industryIds.join(","));
    if (codes.length > 0) args.push("--code", codes.join(","));
    if (benchmarkRaw) args.push("--benchmark", benchmarkRaw);
    console.log(
      `[analysis/industry-corr-offsets/run] python -m analysis.analysis_composites ${args.join(" ")}`,
    );
    const result = await runPythonModule(
      "analysis.analysis_composites",
      args,
      { processIdTag: INDUSTRY_CORR_OFFSET_RUN_TAG },
    );
    // A successful filtered run changes the table's materialized coverage —
    // drop the cached industries/benchmarks lists so the page sees it.
    if (result.success) invalidateIndustryCorrOffsetCoverage();
    res.json({
      success: result.success,
      already_running: result.already_running === true,
      process_id_tag: INDUSTRY_CORR_OFFSET_RUN_TAG,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[analysis/industry-corr-offsets/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

export default router;
