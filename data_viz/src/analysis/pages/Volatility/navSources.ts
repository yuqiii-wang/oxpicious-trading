/**
 * Volatility page nav/codes API wiring — the themes sources and the
 * codes fetcher behind the page's SecCodesPageShell. Split out of
 * index.tsx so the page file only exports the component (the
 * MarketCrowdedness/navSources.ts precedent).
 *
 * The nav trees reuse the shared classification endpoints the
 * market-crowdedness page already exposes for the index sec_type (same
 * FULL classification universe, stats.sec_classification type='index')
 * — no duplicate endpoint. The CODES list is data-scoped instead:
 * fetchVolatilityCodes serves the codes that have vol rows (the
 * pipeline's 10 study indices).
 */
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchMarketCrowdednessStrategyThemes,
  fetchMarketCrowdednessThemes,
  fetchVolatilityCodes,
} from "@/lib/api-client";
import type { VolatilitySecType } from "@/lib/api-client";

/** Nav trees endpoints for the page's index-only toggle. */
export const VOLATILITY_THEMES_SOURCES: Partial<
  Record<SecNavSecType, SecNavThemesSource>
> = {
  index: {
    themes: (exchange) => fetchMarketCrowdednessThemes("index", exchange),
    strategyThemes: (exchange) =>
      fetchMarketCrowdednessStrategyThemes("index", exchange),
  },
};

/** Codes fetcher for SecCodesPageShell — the vol rows' codes for the
 *  toggle's sec_type (index for now); the shell filters client-side by
 *  the nav selection. The exchange arg is kept for the shell's
 *  FetchSecCodes contract but unused — the codes endpoint has no
 *  exchange split. */
export function fetchVolatilityPageCodes(
  secType: Exclude<SecNavSecType, "all">,
  exchange: string | null,
) {
  void exchange;
  return fetchVolatilityCodes(secType as VolatilitySecType);
}
