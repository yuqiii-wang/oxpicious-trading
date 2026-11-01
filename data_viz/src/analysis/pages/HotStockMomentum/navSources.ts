/**
 * Hot Stock Momentum nav/codes API wiring — the themes sources and the
 * codes fetcher behind the page's SecCodesPageShell. Split out of
 * index.tsx so the page file only exports the component
 * (react-refresh/only-export-components — the valuationNav.ts
 * precedent).
 */
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import {
  fetchHotStockMomentumCodes,
  fetchHotStockMomentumStrategyThemes,
  fetchHotStockMomentumThemes,
} from "@/lib/api-client";
import type { HotStockMomentumSecType } from "@shared/types";

/** Nav trees endpoints per sec_type — the page's toggle lists index and
 *  industry ONLY. */
export const HOT_STOCK_MOMENTUM_THEMES_SOURCES: Record<
  HotStockMomentumSecType,
  SecNavThemesSource
> = {
  index: {
    themes: (exchange) => fetchHotStockMomentumThemes("index", exchange),
    strategyThemes: (exchange) =>
      fetchHotStockMomentumStrategyThemes("index", exchange),
  },
  industry: {
    themes: (exchange) => fetchHotStockMomentumThemes("industry", exchange),
    strategyThemes: (exchange) =>
      fetchHotStockMomentumStrategyThemes("industry", exchange),
  },
};

/** Codes fetcher for SecCodesPageShell — the narrowing cast is safe: the
 *  shell's toggle lists never contain anything but index/industry here. */
export function fetchHotStockMomentumPageCodes(
  secType: Exclude<SecNavSecType, "all">,
  exchange: string | null,
) {
  return fetchHotStockMomentumCodes(secType as HotStockMomentumSecType, exchange);
}
