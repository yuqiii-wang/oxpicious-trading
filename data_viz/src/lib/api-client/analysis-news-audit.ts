/**
 * API client for the News Audit composite — reads
 * text.news_provider_audit (+ registry) and text.news_content_audit.
 */
import { fetchJson } from "./_cache";
import type { AnalysisRunResponse } from "./analysis-run";
import type {
  NewsContentAuditResponse,
  NewsCoverageAuditResponse,
  NewsProviderAuditResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  GET providers — one row per (5-day window, origin) over the most recent
//  `limitWindows` windows; `source` (optional) filters to one provider.
// ---------------------------------------------------------------------------
export function fetchNewsProviderAudit(
  limitWindows: number,
  source = "",
): Promise<NewsProviderAuditResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limitWindows));
  if (source) params.set("source", source);
  const qs = params.toString();
  return fetchJson<NewsProviderAuditResponse>(
    `/api/analysis/news-audit/providers?${qs}`,
  );
}

// ---------------------------------------------------------------------------
//  GET content — one row per (5-day window, industry) over the most recent
//  `limitWindows` windows; `industryId` (optional) filters to one industry.
// ---------------------------------------------------------------------------
export function fetchNewsContentAudit(
  limitWindows: number,
  industryId = "",
): Promise<NewsContentAuditResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limitWindows));
  if (industryId) params.set("industry_id", industryId);
  const qs = params.toString();
  return fetchJson<NewsContentAuditResponse>(
    `/api/analysis/news-audit/content?${qs}`,
  );
}

// ---------------------------------------------------------------------------
//  Refresh trigger.
//
//  POST /api/analysis/news-audit/run spawns
//  `python -m analysis.news_audit` (incremental registry sync + window
//  recompute) via the shared py-runner and waits for it to finish. Drives
//  the refresh button on the Composites → News Audit page. Deduped by a
//  fixed process-id-tag — poll fetchAnalysisRunStatus([NEWS_AUDIT_RUN_TAG])
//  for the spinner. Never throws; failures surface as
//  { success: false, stderr_tail }.
// ---------------------------------------------------------------------------
/** Fixed process-id-tag for UI-triggered news-audit runs — must match the
 *  server-side NEWS_AUDIT_RUN_TAG. */
export const NEWS_AUDIT_RUN_TAG = "analysis-run:news_audit";

/** Trigger an incremental news-audit run (registry + missing/trailing/grace
 *  windows). Resolves when the run finishes (or is deduped —
 *  already_running). */
export async function runNewsAuditRefresh(): Promise<AnalysisRunResponse> {
  try {
    const res = await fetch("/api/analysis/news-audit/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as AnalysisRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}

// ---------------------------------------------------------------------------
//  GET coverage — the DAILY coverage audit (text.news_coverage_audit):
//  one row per (trading day, industry) over the most recent `limitDays`
//  trading days; '000001' = the whole-market rollup row.
// ---------------------------------------------------------------------------
export function fetchNewsCoverageAudit(
  limitDays: number,
): Promise<NewsCoverageAuditResponse> {
  const params = new URLSearchParams();
  params.set("limit_days", String(limitDays));
  const qs = params.toString();
  return fetchJson<NewsCoverageAuditResponse>(
    `/api/analysis/news-audit/coverage?${qs}`,
  );
}
