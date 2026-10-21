/**
 * Barrel re-export for the MA-Spread analysis service — the split of the
 * former monolithic mov-ave-spreads.ts into a directory of smaller files:
 *
 *   sec-sources.ts   sec_type plumbing + per-sec_type chart JOIN config
 *   pairs-config.ts  the 23 pair specs, labels, DB chart row + pickers
 *   ohlc-pivot.ts    long→wide OHLC pivot SQL fragments
 *   meta.ts          classification meta SQL for codes/themes filtering
 *   codes.ts         listMovAveSpreadCodes
 *   chart.ts         the pairs / ema / shared chart blocks + composition
 *   extras.ts        getMovAveSpreadChartExtras dispatcher + simple groups
 *   ohlc-extras.ts   "ohlc" + "gaps" extras groups
 *   roof-floor.ts    "rfest" + "rfaudit" extras groups
 *   regimes.ts       getMarketRegimeSpans
 *   themes.ts        the two ThemeSelector trees
 */
export { listMovAveSpreadCodes } from "./codes.js";
export {
  getMovAveSpreadChart,
  getMovAveSpreadChartPairs,
  getMovAveSpreadChartEma,
  getMovAveSpreadChartShared,
} from "./chart.js";
export { getMovAveSpreadChartExtras } from "./extras.js";
export { getMarketRegimeSpans } from "./regimes.js";
export {
  listMovAveSpreadThemes,
  listMovAveSpreadStrategyThemes,
} from "./themes.js";
