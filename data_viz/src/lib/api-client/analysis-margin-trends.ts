import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MarginIndustrySeriesResponse,
  MarginAttributionType,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Derivatives — Margin Trends (single-industry RONGZI margin flows)
//  Endpoint feeding the single-plot page:
//    industry-series — per-(security, date) margin series (+ regime label)
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
