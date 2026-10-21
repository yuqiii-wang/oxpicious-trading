/**
 * Barrel re-export for the analysis service modules.
 * Extracted from the former analysis.service.ts.
 */
export { listMovAveSpreadCodes, getMovAveSpreadChart, getMovAveSpreadChartPairs, getMovAveSpreadChartEma, getMovAveSpreadChartShared, getMovAveSpreadChartExtras, getMarketRegimeSpans, listMovAveSpreadThemes, listMovAveSpreadStrategyThemes } from "./mov-ave-spreads/index.js";
export { listPerfAttrCodes, getPerfAttrAttribution, listPerfAttrThemes, getPerfAttrChart, listPerfAttrStrategyThemes } from "./perf-attribution/index.js";
export { listIndustrySentimentsThemes, listIndustrySentimentsStrategyThemes, getIndustrySentimentsChart, getIndustrySentimentsChartByCode } from "./industry-sentiments/index.js";
export { getIndustryCorrelations } from "./industry-correlations.js";
export { getIndustryCorrOffsets, listIndustryCorrOffsetBenchmarks, listIndustryCorrOffsetIndustries, invalidateIndustryCorrOffsetCoverage } from "./industry-corr-offsets.js";
export { listPairPctRatioUniverse, getPairPctRatios } from "./pair-pct-ratios.js";
export { getBenchmarkPriceChart, getIndustryAttributionPriceSeries } from "./benchmark-price-chart.js";
export { getAllIndustriesAttribution, getMemberIndexAttribution } from "./industry-attribution-bars.js";
export { getIndustryBenchmarkAttribution, listIndustryAttributionBenchmarks } from "./industry-benchmark-attribution.js";
export { getIndustryEtfPriceSeries, getIndustryEtfContributionBars } from "./industry-etf-contribution.js";
export { getIndustryHypesAndDrains } from "./industry-hypes-and-drains.js";
export { getIntradayMovements, listIntradayMovementsBenchmarks, listIntradayMovementsDates } from "./intraday-movements.js";
export { getIntradayMovementsPrevDayOhlc } from "./intraday-movements-prev-day-ohlc.js";
export {
  listValuationCodes,
  getPeChart,
  listValuationThemes,
  listValuationStrategyThemes,
  listPeStreaks,
} from "./pe/index.js";
export { listDividendDeliveries } from "./dividends.js";
export {
  listMarginTrendThemes,
  listMarginTrendStrategyThemes,
  listMarginTrendUnderlyings,
  getMarginIndustrySeries,
  getMarginTrends,
} from "./margin-trends.js";
export { getFuturesExt } from "./analysis-futures.service.js";
