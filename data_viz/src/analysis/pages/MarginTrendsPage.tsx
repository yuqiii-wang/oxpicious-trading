/**
 * Thin re-export so existing imports of `@/analysis/pages/MarginTrendsPage`
 * continue to resolve. The actual implementation lives in the
 * `./MarginTrends/` subdirectory (split into smaller modules, mirroring
 * PeAndDividend/).
 */
export { default } from "./MarginTrends";
