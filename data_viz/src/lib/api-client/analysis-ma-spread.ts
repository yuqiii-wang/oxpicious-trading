import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MaSpreadSecType,
  ForecastKind,
  ForecastResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — MA-Spread (ETF + Index)
//  All three endpoints require an `sec_type` query param ('etf' | 'index')
//  and rely on the LRU TTL cache only (no version check; the analysis schema
//  is recomputed offline by analyze_mov_ave_spread.py).
// ---------------------------------------------------------------------------
export function fetchMovAveSpreadCodes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<MovAveSpreadCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<MovAveSpreadCodesResponse>(
    `/api/analysis/mov-ave-spread/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for the ThemeSelector.
 *  Only includes codes that have rows in analysis.mov_ave_spreads_detail.
 *  When `exchange` is set, the tree is filtered at the backend via
 *  matchesExchange() so cross-border securities (HK/Overseas) are excluded
 *  unless explicitly selected. */
export function fetchMovAveSpreadThemes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/mov-ave-spread/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchMovAveSpreadStrategyThemes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/mov-ave-spread/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchMovAveSpreadChart(
  code: string,
  secType: MaSpreadSecType,
): Promise<MovAveSpreadChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<MovAveSpreadChartResponse>(
    `/api/analysis/mov-ave-spread/chart${qs ? `?${qs}` : ""}`,
  );
}

/** Forecast buckets table (2nd plot beneath the spread chart): one code's
 *  mov_rsi / mov_std buckets joined 1:1 with their analysis_forecasts
 *  forecast_results columns. ALL stat_months of the code are returned
 *  (optionally narrowed to stat_months >= `month`). The response lists all
 *  available months for the month tick-filter. */
export function fetchMovAveSpreadForecast(
  code: string,
  secType: MaSpreadSecType,
  kind: ForecastKind,
  month?: string | null,
): Promise<ForecastResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("kind", kind);
  if (month) params.set("month", month);
  return fetchJson<ForecastResponse>(
    `/api/analysis/mov-ave-spread/forecast?${params.toString()}`,
  );
}
