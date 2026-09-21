import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadExtrasResponse,
  MovAveSpreadMetric,
  MaSpreadSecType,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — MA-Spread (ETF + Index)
//  All endpoints require an `sec_type` query param ('etf' | 'index' |
//  'stock') and rely on the LRU TTL cache only (no version check; the
//  analysis schema is recomputed offline by analyze_mov_ave_spread.py).
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

/** On-demand metric groups for the MA-Spread panel. Each group feeds one
 *  control section (amt → Amt/MA chips, ohlc → OHLC Window,
 *  streaks → High/Low Streaks, pxvol → Px-Vol States) and is fetched the
 *  first time one of its buttons is picked. */
export function fetchMovAveSpreadExtras(
  code: string,
  secType: MaSpreadSecType,
  metrics: MovAveSpreadMetric[],
): Promise<MovAveSpreadExtrasResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (metrics.length > 0) params.set("metrics", metrics.join(","));
  const qs = params.toString();
  return fetchJson<MovAveSpreadExtrasResponse>(
    `/api/analysis/mov-ave-spread/extras${qs ? `?${qs}` : ""}`,
  );
}
