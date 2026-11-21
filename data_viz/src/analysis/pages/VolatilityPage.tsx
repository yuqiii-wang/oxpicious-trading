/**
 * Thin re-export so existing imports of `@/analysis/pages/VolatilityPage`
 * continue to resolve. The actual implementation lives in the
 * `./Volatility/` subdirectory (mirroring PePage / MarketBetaPage /
 * MarketCrowdednessPage).
 */
export { default } from "./Volatility";
