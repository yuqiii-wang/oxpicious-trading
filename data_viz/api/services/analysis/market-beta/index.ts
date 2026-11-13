/**
 * Barrel re-export for the Market Beta analysis service — the
 * split of the former monolithic market-beta.ts into a directory
 * of smaller files:
 *
 *   meta.ts          cached per-index classification rows (composition-only)
 *   themes.ts        the two ThemeSelector trees
 *   chart.ts         per-industry / per-code close series + aggregation + benchmarks
 *   participation.ts the Market Participation panel series (whole-market
 *                    turnover / SSE share / advance-decline breadth)
 */
export {
  listMarketBetaThemes,
  listMarketBetaStrategyThemes,
} from "./themes.js";
export {
  getMarketBetaChart,
  getMarketBetaChartByCode,
} from "./chart.js";
export {
  getMarketParticipation,
  type MarketParticipationRow,
} from "./participation.js";
