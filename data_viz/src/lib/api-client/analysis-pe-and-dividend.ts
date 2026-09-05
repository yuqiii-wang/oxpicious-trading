import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  PeAndDividendSecType,
  PeAndDividendCodesResponse,
  PeAndDividendChartResponse,
  PeAndDividendStatsResponse,
  PeAndDividendStreaksResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — PE & Dividend Yield (Index + ETF + Stock)
//  All endpoints require a `sec_type` query param ('etf' | 'index' | 'stock')
//  and rely on the LRU TTL cache only (no version check; the analysis schema
//  is recomputed offline by the Python build script).
// ---------------------------------------------------------------------------
export function fetchPeAndDividendCodes(
  secType: PeAndDividendSecType,
  exchange?: string | null,
): Promise<PeAndDividendCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<PeAndDividendCodesResponse>(
    `/api/analysis/pe-and-dividend/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav.
 *  Only includes codes that have rows in analysis.pe_and_dividends. */
export function fetchPeAndDividendThemes(
  secType: PeAndDividendSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/pe-and-dividend/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchPeAndDividendStrategyThemes(
  secType: PeAndDividendSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/pe-and-dividend/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchPeAndDividendChart(
  code: string,
  secType: PeAndDividendSecType,
): Promise<PeAndDividendChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PeAndDividendChartResponse>(
    `/api/analysis/pe-and-dividend/chart${qs ? `?${qs}` : ""}`,
  );
}

export function fetchPeAndDividendStats(
  code: string,
  secType: PeAndDividendSecType,
): Promise<PeAndDividendStatsResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PeAndDividendStatsResponse>(
    `/api/analysis/pe-and-dividend/stats${qs ? `?${qs}` : ""}`,
  );
}

/** Band-BREAK excursion streaks of the code's pe_ma20 / dividend_yield
 *  series (analysis.pe_and_dividend_pct_streaks, side derived at query
 *  time), flat for ALL (metric, period, pct_type) combos. */
export function fetchPeAndDividendStreaks(
  code: string,
  secType: PeAndDividendSecType,
): Promise<PeAndDividendStreaksResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PeAndDividendStreaksResponse>(
    `/api/analysis/pe-and-dividend/streaks${qs ? `?${qs}` : ""}`,
  );
}
