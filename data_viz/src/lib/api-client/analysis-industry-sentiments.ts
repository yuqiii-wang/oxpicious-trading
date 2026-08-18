import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  IndustrySentimentsChartResponse,
  IndustryCorrelationsResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — Industry Sentiments (member index values, rebased to 100)
//  TTL-only cache. NO analysis-table intermediary — the data is queried
//  directly from stats.index_basic_stats JOIN stats.sec_classification at
//  request time. The frontend rebases each member index to 100 at the start
//  of the displayed (zoom) window (scale-invariant comparison across indices
//  with different absolute price levels).
// ---------------------------------------------------------------------------
/** Themes tree (L1 sector → L2 industry) for the ThemeSelector.
 *  Built directly from stats.sec_classification (type='index'). Each
 *  industry's chip count = number of member indices in that industry.
 *  The optional `exchange` filter narrows the tree to the selected exchange
 *  group (PRIMARY/SS/SZ/BJ/HK/OVERSEAS) — mirroring the index-baseline themes
 *  endpoint. */
export function fetchIndustrySentimentsThemes(
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/industry-sentiments/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchIndustrySentimentsStrategyThemes(
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/industry-sentiments/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

/**
 * Fetch per-index close time series for ONE industry. Returns one entry per
 * member index in the industry, each with its raw daily close series from
 * stats.index_basic_stats. The frontend rebases each to 100 at the start of
 * the visible (zoom) window.
 */
export function fetchIndustrySentimentsChart(
  industryId: string,
): Promise<IndustrySentimentsChartResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  const qs = params.toString();
  return fetchJson<IndustrySentimentsChartResponse>(
    `/api/analysis/industry-sentiments/chart?${qs}`,
  );
}

/** Fetch chart data (close series) for a single index code. Used when an
 *  L3 index chip is clicked under a strategy/theme — strategy-primary
 *  indices may not have an industry_id classification. */
export function fetchIndustrySentimentsChartByCode(
  code: string,
): Promise<IndustrySentimentsChartResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  return fetchJson<IndustrySentimentsChartResponse>(
    `/api/analysis/industry-sentiments/chart-by-code?${params.toString()}`,
  );
}

/**
 * Fetch pairwise rolling correlation time series between selected
 * industries' mean_price series. Returns one row per (date, pair) for
 * every lexicographic (a<b) pair from `industryIds`, with corr_5d /
 * corr_20d / corr_60d / corr_255d. Drives the expandable Correlation chart
 * on the IndustrySentiments page — only enabled when ≥2 industries are
 * selected.
 *
 * `poolSize` selects the same-pool slice for both endpoints (cross-pool
 * comparisons are not materialized). Defaults to 'all'.
 */
export function fetchIndustryCorrelations(
  industryIds: string[],
  poolSize: "all" | "small" | "mid" | "large" = "all",
): Promise<IndustryCorrelationsResponse> {
  const params = new URLSearchParams();
  if (industryIds.length > 0) params.set("industry_ids", industryIds.join(","));
  params.set("pool_size", poolSize);
  const qs = params.toString();
  return fetchJson<IndustryCorrelationsResponse>(
    `/api/analysis/industry-correlations?${qs}`,
  );
}
