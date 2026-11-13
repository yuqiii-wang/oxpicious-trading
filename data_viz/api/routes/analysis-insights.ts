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
import { HttpError, wrapAsync } from "../lib/http.js";
import {
  listInsightTemplates,
  listInsightPageDocs,
  resolveInsightMetrics,
} from "../services/analysis/analysis-insights.service.js";

const router = Router();

router.get("/templates", wrapAsync(async (_req, res) => {
  res.json(await listInsightTemplates());
}));

router.get("/page-docs", wrapAsync(async (_req, res) => {
  res.json(await listInsightPageDocs());
}));

router.get("/metrics", wrapAsync(async (req, res) => {
  const { tab, page } = req.query;
  if (typeof tab !== "string" || typeof page !== "string" || !tab || !page) {
    throw new HttpError(400, "MISSING_PARAM", "Missing 'tab'/'page' parameters");
  }
  const str = (v: unknown): string | undefined =>
    typeof v === "string" && v.trim() ? v.trim() : undefined;
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
}));

export default router;
