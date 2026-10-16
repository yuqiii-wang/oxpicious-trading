/**
 * GET /api/analysis-advice — the shared Analysis Advice lookup.
 *
 * Query params:
 *   analysis_item (required) — the analysis item / signal family
 *     ('mov_rsi' | 'bollinger' | 'ma_cross' | ... — the UI Signal picker
 *     values; open vocabulary).
 *   sec_type (required) — 'etf' | 'index' | 'stock'.
 *   code (optional) — the security ticker (indices/ETFs bare '000001',
 *     stocks suffixed '600008.SS'); absent = scope-wide advice only.
 *   industry (optional) — the industry_id scope; resolved server-side from
 *     stats.sec_classification when absent and code is given.
 *   custom_text (optional) — the PK disambiguator; default ''.
 *
 * Returns { analysis_item, sec_type, code, industry, advice: [...] } with
 * advice ordered most-specific first (code match over generic, industry
 * match over unscoped).
 */
import { Router, type Request, type Response } from "express";
import { getAnalysisAdvice } from "../services/analysis-advice.service.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["index", "etf", "stock"]);

router.get("/", async (req: Request, res: Response) => {
  try {
    const analysisItem =
      typeof req.query.analysis_item === "string"
        ? req.query.analysis_item.trim()
        : "";
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type.trim().toLowerCase()
      : "";
    const code = typeof req.query.code === "string"
      ? req.query.code.trim()
      : "";
    const industry = typeof req.query.industry === "string"
      ? req.query.industry.trim()
      : undefined;
    const customText = typeof req.query.custom_text === "string"
      ? req.query.custom_text.trim()
      : "";
    if (!analysisItem) {
      res.status(400).json({
        error: "Missing 'analysis_item' parameter (e.g. mov_rsi / bollinger / ma_cross).",
      });
      return;
    }
    if (!VALID_SEC_TYPES.has(secType)) {
      res.status(400).json({
        error: `Invalid or missing 'sec_type': ${secType}. Expected 'index', 'etf', or 'stock'.`,
      });
      return;
    }
    const { industry: resolved, advice } = await getAnalysisAdvice(
      analysisItem,
      secType,
      code,
      industry,
      customText,
    );
    res.json({
      analysis_item: analysisItem,
      sec_type: secType,
      code,
      industry: resolved,
      advice,
    });
  } catch (err) {
    console.error("[analysis-advice] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
