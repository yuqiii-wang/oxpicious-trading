/**
 * Shared nav/codes API wiring for the valuation-universe pages (PE +
 * Dividends). Both pages render over SecCodesPageShell with the SAME
 * (sec_type, code) universe — analysis.pe ∪ analysis.dividends, built
 * together by analyze.pe_and_dividends — so the themes sources, the codes
 * fetcher, and the refresh cache invalidation are defined ONCE here
 * (2026-09-29; previously each page duplicated an identical THEMES_SOURCES
 * block pointing at the pe-named endpoints).
 */
import type { SecNavThemesSource } from "@/shared/components/sec-nav";
import type { SecNavSecType } from "@/shared/components/sec-nav";
import {
  fetchValuationCodes,
  fetchValuationThemes,
  fetchValuationStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";

/** Nav trees endpoints per sec_type — the shared valuation universe. */
export const VALUATION_THEMES_SOURCES: Record<SecNavSecType, SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchValuationThemes("etf", exchange),
    strategyThemes: (exchange) => fetchValuationStrategyThemes("etf", exchange),
  },
  index: {
    themes: (exchange) => fetchValuationThemes("index", exchange),
    strategyThemes: (exchange) => fetchValuationStrategyThemes("index", exchange),
  },
  stock: {
    themes: (exchange) => fetchValuationThemes("stock", exchange),
    strategyThemes: (exchange) => fetchValuationStrategyThemes("stock", exchange),
  },
};

/** Codes fetcher for SecCodesPageShell — /api/analysis/valuation/codes. */
export function fetchValuationPageCodes(
  secType: SecNavSecType,
  exchange: string | null,
) {
  return fetchValuationCodes(secType, exchange);
}

/** Refresh invalidation — drop the cached valuation-universe responses. */
export function invalidateValuationCache(): void {
  invalidateCacheForPrefix("/api/analysis/valuation/");
}
