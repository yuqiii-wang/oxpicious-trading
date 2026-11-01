/**
 * Market Crowdedness nav/codes API wiring — the themes sources and the
 * codes fetcher behind the page's SecCodesPageShell. Split out of
 * index.tsx so the page file only exports the component
 * (react-refresh/only-export-components — the valuationNav.ts
 * precedent).
 */
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchMarketCrowdednessCodes,
  fetchMarketCrowdednessStrategyThemes,
  fetchMarketCrowdednessThemes,
} from "@/lib/api-client";
import type { MarketCrowdednessSecType } from "@shared/types";

/** Nav trees endpoints per sec_type — the page's toggle lists index and
 *  industry ONLY. */
export const MARKET_CROWDEDNESS_THEMES_SOURCES: Record<
  MarketCrowdednessSecType,
  SecNavThemesSource
> = {
  index: {
    themes: (exchange) => fetchMarketCrowdednessThemes("index", exchange),
    strategyThemes: (exchange) =>
      fetchMarketCrowdednessStrategyThemes("index", exchange),
  },
  industry: {
    themes: (exchange) => fetchMarketCrowdednessThemes("industry", exchange),
    strategyThemes: (exchange) =>
      fetchMarketCrowdednessStrategyThemes("industry", exchange),
  },
};

/** Codes fetcher for SecCodesPageShell — the narrowing cast is safe: the
 *  shell's toggle lists never contain anything but index/industry here. */
export function fetchMarketCrowdednessPageCodes(
  secType: Exclude<SecNavSecType, "all">,
  exchange: string | null,
) {
  return fetchMarketCrowdednessCodes(secType as MarketCrowdednessSecType, exchange);
}
