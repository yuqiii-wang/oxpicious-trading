import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MarginIndustrySeriesResponse,
  MarginTrendsShadeResponse,
  MarginAttributionType,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Derivatives — Margin Trends (single-industry RONGZI margin flows)
//  Endpoints feeding the single-plot page:
//    industry-series — per-(security, date) margin series
//    trends          — trend episodes (shade overlay + ratio)
// ---------------------------------------------------------------------------
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
): Promise<MarginIndustrySeriesResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  params.set("attribution", attribution);
  const qs = params.toString();
  return fetchJson<MarginIndustrySeriesResponse>(
    `/api/analysis/margin-trends/industry-series?${qs}`,
  );
}

export function fetchMarginTrends(
  industryId: string,
  attribution: MarginAttributionType,
): Promise<MarginTrendsShadeResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  params.set("attribution", attribution);
  return fetchJson<MarginTrendsShadeResponse>(
    `/api/analysis/margin-trends/trends?${params.toString()}`,
  );
}
