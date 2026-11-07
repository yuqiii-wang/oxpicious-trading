/**
 * Margin Trends + Futures Ext routes (/margin-trends/*, /futures/ext).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listMarginTrendThemes,
  listMarginTrendStrategyThemes,
  listMarginTrendUnderlyings,
  getMarginIndustrySeries,
  getMarginTrends,
  getFuturesExt,
} from "../../services/analysis/index.js";

const router = Router();

// ---- Margin Trends (single-industry RONGZI margin flows)
//   analysis.margin_index_series (TABLE) — weighted-avg constituent-stock
//                                          margin per (index_code, date),
//                                          built by Python vectorization
//   analysis.margin_changes             — trend episodes (shade overlay)
//
//   GET /api/analysis/margin-trends/themes
//     L1 sector → L2 industry tree (industries WITH margin data).
//   GET /api/analysis/margin-trends/strategy-themes
//     Parallel L1 strategy → L2 theme tree (RIGHT column).
//   GET /api/analysis/margin-trends/industry-series?industry_id=BANKS&attribution=index
//     Per-(security, date) margin series for one industry + attribution.
//     'index' reads the margin_index_series TABLE; 'etf' reads
//     stats.etf_liquidity_margin for the industry's ETFs; 'industry'
//     reads analysis.margin_industry_stats (the industry's OWN margin
//     curve, ONE series with code = industry_id; ?normalize=true switches
//     it to the SUM of the per-side member AVERAGES — the *_norm cols).
router.get("/margin-trends/themes", async (req: Request, res: Response) => {
  try {
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    res.json(await listMarginTrendThemes(attribution));
  } catch (err) {
    console.error("[analysis/margin-trends/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/margin-trends/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listMarginTrendStrategyThemes());
  } catch (err) {
    console.error("[analysis/margin-trends/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/margin-trends/underlyings
//     ALL broad-market index codes (stats.sec_index_tags.is_broad_market)
//     for the exchange attribution's mapped-underlying dropdown.
router.get("/margin-trends/underlyings", async (req: Request, res: Response) => {
  try {
    res.json(await listMarginTrendUnderlyings());
  } catch (err) {
    console.error("[analysis/margin-trends/underlyings] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/margin-trends/industry-series", async (req: Request, res: Response) => {
  try {
    const industryId = (req.query.industry_id as string | undefined) ?? "";
    if (!industryId.trim()) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    // 'exchange' only — the mapped broad-market underlying for the
    // close / turnover / regime joins (validated + defaulted in the SQL).
    const underlying = req.query.underlying as string | undefined;
    // 'industry' only — normalized curve: SUM of the per-side member
    // AVERAGES (*_norm cols in margin_industry_stats) instead of the
    // raw SUM(stock+etf).
    const normalize = req.query.normalize as string | undefined;
    res.json(await getMarginIndustrySeries(industryId, attribution, underlying, normalize));
  } catch (err) {
    console.error("[analysis/margin-trends/industry-series] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/analysis/margin-trends/trends?industry_id=BANKS&attribution=index
//     Sustained UP/DOWN trend episode date ranges for the securities in one
//     industry + attribution. Used by the 1st plot to render light shade
//     (markArea) over each trend window.
router.get("/margin-trends/trends", async (req: Request, res: Response) => {
  try {
    const industryId = (req.query.industry_id as string | undefined) ?? "";
    if (!industryId.trim()) {
      res.status(400).json({ error: "Missing 'industry_id' parameter" });
      return;
    }
    const attribution = (req.query.attribution as string | undefined) ?? "index";
    res.json(await getMarginTrends(industryId, attribution));
  } catch (err) {
    console.error("[analysis/margin-trends/trends] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Futures Analysis — basis + correlation per (date, code) for a product
//   analysis.futures_ext JOIN stats.futures_identity on product_code
//
//   GET /api/analysis/futures/ext?product=IF
//     Returns FuturesExtResponse: gapByCodeDate (for 1st plot tooltip),
//     corrByCodeDate (for 2nd plot correlation chart), and flat rows.
router.get("/futures/ext", async (req: Request, res: Response) => {
  try {
    const product = (req.query.product as string | undefined) ?? "";
    if (!product.trim()) {
      res.status(400).json({ error: "Missing 'product' parameter" });
      return;
    }
    const ext = await getFuturesExt(product);
    // Serialize Maps to plain objects for JSON
    const gapObj: Record<string, Record<string, number | null>> = {};
    ext.gapByCodeDate.forEach((dateMap, code) => {
      gapObj[code] = {};
      dateMap.forEach((val, date) => { gapObj[code][date] = val; });
    });
    const corrObj: Record<string, Record<string, number | null>> = {};
    ext.corrByCodeDate.forEach((dateMap, code) => {
      corrObj[code] = {};
      dateMap.forEach((val, date) => { corrObj[code][date] = val; });
    });
    res.json({
      product: ext.product,
      gapByCodeDate: gapObj,
      corrByCodeDate: corrObj,
      rows: ext.rows,
    });
  } catch (err) {
    console.error("[analysis/futures/ext] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
