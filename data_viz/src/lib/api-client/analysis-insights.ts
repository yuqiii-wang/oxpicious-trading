/**
 * analysis-insights client — the Ask AI insight metrics.
 *
 * GET /api/analysis-insights/metrics resolves one analysis page's
 * registered metrics of particular interest (analysis_insight_metrics.
 * insight_templates, INSERT-seeded by database/sql/analysis/static/
 * analysis_insight_metrics/02..05 — the per-tab data files) for an ask context:
 * the classification-nav chosen security (code + secType), an optional
 * industry id, and the as-of date chosen in the code trend / modal
 * (exact "YYYY-MM-DD" or year-month "YYYY-MM").
 *
 * Plain fetch (not fetchJson): the compute metrics may spawn a WSL
 * python process server-side, so allow a generous timeout and let the
 * modal render the fast table metrics first (the response returns all
 * metrics at once — values arriving "–" are the python-unavailable
 * case, never a hang client-side).
 */
import type { AiAskInsightMetric } from "@/shared/ai-ask";

export interface InsightMetricsResponse {
  tab: string;
  page: string;
  page_doc: {
    tab: string;
    page: string;
    title: string;
    doc: string | null;
    keywords: string[];
  } | null;
  code: string | null;
  sec_type: string | null;
  industry: string | null;
  as_of: string | null;
  metrics: AiAskInsightMetric[];
}

const INSIGHT_METRICS_TIMEOUT_MS = 60_000;

export async function fetchInsightMetrics(params: {
  tab: string;
  page: string;
  code?: string;
  secType?: string;
  industry?: string;
  date?: string;
}): Promise<InsightMetricsResponse> {
  const qs = new URLSearchParams({ tab: params.tab, page: params.page });
  if (params.code) qs.set("code", params.code);
  if (params.secType) qs.set("secType", params.secType);
  if (params.industry) qs.set("industry", params.industry);
  if (params.date) qs.set("date", params.date);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), INSIGHT_METRICS_TIMEOUT_MS);
  try {
    const res = await fetch(`/api/analysis-insights/metrics?${qs}`, {
      signal: controller.signal,
    });
    const text = await res.text();
    if (!res.ok) {
      throw new Error(
        `insight metrics failed (HTTP ${res.status}): ${text.slice(0, 200)}`,
      );
    }
    return JSON.parse(text) as InsightMetricsResponse;
  } finally {
    clearTimeout(timer);
  }
}
