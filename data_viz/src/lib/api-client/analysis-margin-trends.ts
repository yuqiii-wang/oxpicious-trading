import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MarginIndustrySeriesResponse,
  MarginUnderlyingEntry,
  MarginAttributionType,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Derivatives — Margin Trends (single-industry RONGZI margin flows)
//  Endpoints feeding the single-plot page:
//    underlyings — ALL broad-market codes for the exchange attribution's
//                  mapped-underlying dropdown
//    industry-series — per-(security, date) margin series (+ regime label);
//                  'exchange' takes ?underlying= (broad-market code whose
//                  close/turnover/regime the daily-return views map onto)
// ---------------------------------------------------------------------------
export function fetchMarginTrendUnderlyings(): Promise<MarginUnderlyingEntry[]> {
  return fetchJson<MarginUnderlyingEntry[]>(
    `/api/analysis/margin-trends/underlyings`,
  );
}

export function fetchMarginTrendThemes(
  attribution: MarginAttributionType = "index",
): Promise<SectorNode[]> {
  return fetchJson<SectorNode[]>(
    `/api/analysis/margin-trends/themes?attribution=${attribution}`,
  );
}

export function fetchMarginTrendStrategyThemes(): Promise<StrategyNode[]> {
  return fetchJson<StrategyNode[]>("/api/analysis/margin-trends/strategy-themes");
}

export function fetchMarginIndustrySeries(
  industryId: string,
  attribution: MarginAttributionType,
  underlyingCode?: string | null,
  normalize?: boolean,
): Promise<MarginIndustrySeriesResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  params.set("attribution", attribution);
  if (underlyingCode && attribution === "exchange") {
    params.set("underlying", underlyingCode);
  }
  // 'industry' only — the per-member-normalized curve (SUM of the
  // per-side member averages from the *_norm columns).
  if (normalize && attribution === "industry") {
    params.set("normalize", "true");
  }
  const qs = params.toString();
  return fetchJson<MarginIndustrySeriesResponse>(
    `/api/analysis/margin-trends/industry-series?${qs}`,
  );
}
