/**
 * Analysis Composites — News Text States (新闻文本状态).
 * The chart/nav/event endpoints behind
 * /analysis/composites/news-text-states. text.news_text_daily is built
 * by analysis.news_text_daily (the nightly analysis_daily tier); the
 * text schema is recomputed offline by the Python pipeline, so the
 * client relies on the LRU TTL cache only (no version check).
 */
import { fetchJson } from "./_cache";
import type {
  NewsTextStatesChartResponse,
  NewsTextStatesDisagreementResponse,
  NewsTextStatesEventsResponse,
  NewsTextStatesRecentLevelsResponse,
  NewsTextStatesScopesResponse,
  NewsTextStatesStrongNegEventsResponse,
} from "@shared/types";

/** Scopes with rows (market kinds + industries + the sector rollup) —
 *  the page selector source. */
export function fetchNewsTextStatesScopes(
): Promise<NewsTextStatesScopesResponse> {
  return fetchJson<NewsTextStatesScopesResponse>(
    "/api/analysis/news-text-states/scopes",
  );
}

/** One scope's daily state panel + close context. Defaults to the
 *  market_all scope (kind 0, code '000001'); kind 2 = industry, kind 3
 *  = sector. */
export function fetchNewsTextStatesChart(
  scopeKind?: number,
  scopeCode?: string,
): Promise<NewsTextStatesChartResponse> {
  const params = new URLSearchParams();
  if (scopeKind != null) params.set("scope_kind", String(scopeKind));
  if (scopeCode) params.set("scope_code", scopeCode);
  const qs = params.toString();
  return fetchJson<NewsTextStatesChartResponse>(
    `/api/analysis/news-text-states/chart${qs ? `?${qs}` : ""}`,
  );
}

/** Recent industry event days across industries (newest first). */
export function fetchNewsTextStatesEvents(
  limit?: number,
): Promise<NewsTextStatesEventsResponse> {
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  const qs = params.toString();
  return fetchJson<NewsTextStatesEventsResponse>(
    `/api/analysis/news-text-states/events${qs ? `?${qs}` : ""}`,
  );
}

/** Daily lexicon-vs-LLM disagreement counts (the ambiguity flag). */
export function fetchNewsTextStatesDisagreement(
  windowDays?: number,
): Promise<NewsTextStatesDisagreementResponse> {
  const params = new URLSearchParams();
  if (windowDays != null) params.set("window_days", String(windowDays));
  const qs = params.toString();
  return fetchJson<NewsTextStatesDisagreementResponse>(
    `/api/analysis/news-text-states/disagreement${qs ? `?${qs}` : ""}`,
  );
}

/** Recent strong-negative industry event days (level ≤ −1.5 — the
 *  validated reversal-event cut). */
export function fetchNewsTextStatesStrongNegEvents(
  windowDays?: number,
  limit?: number,
): Promise<NewsTextStatesStrongNegEventsResponse> {
  const params = new URLSearchParams();
  if (windowDays != null) params.set("window_days", String(windowDays));
  if (limit != null) params.set("limit", String(limit));
  const qs = params.toString();
  return fetchJson<NewsTextStatesStrongNegEventsResponse>(
    `/api/analysis/news-text-states/strong-neg-events${qs ? `?${qs}` : ""}`,
  );
}

/** The cross-section: per industry/sector scope its latest event-day
 *  reading in the window (the bar overview's source; scopes with no
 *  event day in the window are absent). */
export function fetchNewsTextStatesRecentLevels(
  windowDays?: number,
): Promise<NewsTextStatesRecentLevelsResponse> {
  const params = new URLSearchParams();
  if (windowDays != null) params.set("window_days", String(windowDays));
  const qs = params.toString();
  return fetchJson<NewsTextStatesRecentLevelsResponse>(
    `/api/analysis/news-text-states/recent-levels${qs ? `?${qs}` : ""}`,
  );
}
