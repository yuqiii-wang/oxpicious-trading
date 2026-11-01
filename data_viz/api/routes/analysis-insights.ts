/**
 * analysis-insights routes — the Ask AI insight registry.
 *
 *   GET /api/analysis-insights/templates
 *       The full registry (analysis_insight_metrics.insight_templates, INSERT-seeded
 *       by database/sql/analysis/static/analysis_insight_metrics/02..05 — the
 *       per-tab data files) — tab → page →
 *       metric identity, labels and source specs.
 *
 *   GET /api/analysis-insights/metrics?tab=&page=[&code=&secType=&industry=&date=]
 *       Resolved metric VALUES for one analysis page and ask context (the
 *       classification-nav security + the as-of date chosen in the code
 *       trend / modal). date accepts YYYY-MM-DD or YYYY-MM.
 */
import { Router } from "express";
import {
  listInsightTemplates,
  listInsightPageDocs,
  resolveInsightMetrics,
} from "../services/analysis-insights.service.js";

const router = Router();

router.get("/templates", async (_req, res) => {
  try {
    res.json(await listInsightTemplates());
  } catch (err) {
    console.error("[analysis-insights/templates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/page-docs", async (_req, res) => {
  try {
    res.json(await listInsightPageDocs());
  } catch (err) {
    console.error("[analysis-insights/page-docs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/metrics", async (req, res) => {
  const { tab, page } = req.query;
  if (typeof tab !== "string" || typeof page !== "string" || !tab || !page) {
    res.status(400).json({ error: "Missing 'tab'/'page' parameters" });
    return;
  }
  const str = (v: unknown): string | undefined =>
    typeof v === "string" && v.trim() ? v.trim() : undefined;
  try {
    res.json(
      await resolveInsightMetrics({
        tab,
        page,
        code: str(req.query.code),
        secType: str(req.query.secType),
        industry: str(req.query.industry),
        date: str(req.query.date),
      }),
    );
  } catch (err) {
    console.error("[analysis-insights/metrics] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
