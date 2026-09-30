/**
 * Analysis Commons — PE chart + band-break streaks (PE page's per-security
 * analysis card). The SHARED universe endpoints both valuation pages use
 * (codes / themes / strategy-themes) live in ./analysis-valuation.ts
 * (/api/analysis/valuation/* — 2026-09-29 rename from the pe-named paths).
 * All endpoints require a `sec_type` query param ('etf' | 'index' |
 * 'stock') and rely on the LRU TTL cache only (no version check; the
 * analysis schema is recomputed offline by the Python build script).
 */
import { fetchJson } from "./_cache";
import type {
  PeSecType,
  PeChartResponse,
  PeStreaksResponse,
} from "@shared/types";

export function fetchPeChart(
  code: string,
  secType: PeSecType,
): Promise<PeChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PeChartResponse>(
    `/api/analysis/pe/chart${qs ? `?${qs}` : ""}`,
  );
}

/** Band-BREAK excursion streaks of the code's pe series
 *  (analysis.pe_pct_streaks, side derived at query time), flat for ALL
 *  (period, pct_type) combos. */
export function fetchPeStreaks(
  code: string,
  secType: PeSecType,
): Promise<PeStreaksResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PeStreaksResponse>(
    `/api/analysis/pe/streaks${qs ? `?${qs}` : ""}`,
  );
}
