/**
 * Analysis Commons — Valuation universe (PE ∪ Dividend Yield, Index + ETF
 * + Stock). The SHARED nav/codes endpoints consumed by BOTH the PE and
 * Dividends pages' SecCodesPageShell (2026-09-29; formerly the pe-named
 * /api/analysis/pe/codes|themes|strategy-themes). analysis.pe and
 * analysis.dividends are built together by analysis.pe_and_dividends over
 * the same (sec_type, code) universe, so one endpoint set serves both
 * pages. All endpoints require a `sec_type` query param ('etf' | 'index' |
 * 'stock') and rely on the LRU TTL cache only (no version check; the
 * analysis schema is recomputed offline by the Python build script).
 */
import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  PeSecType,
  ValuationCodesResponse,
} from "@shared/types";

export function fetchValuationCodes(
  secType: PeSecType,
  exchange?: string | null,
): Promise<ValuationCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<ValuationCodesResponse>(
    `/api/analysis/valuation/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav.
 *  Only includes codes that have rows in analysis.pe / analysis.dividends. */
export function fetchValuationThemes(
  secType: PeSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/valuation/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchValuationStrategyThemes(
  secType: PeSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/valuation/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}
