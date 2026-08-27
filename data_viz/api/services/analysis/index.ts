/**
 * Barrel re-export for the analysis service modules.
 * Extracted from the former analysis.service.ts.
 */
export { listMovAveSpreadCodes, getMovAveSpreadChart, listMovAveSpreadThemes, listMovAveSpreadStrategyThemes } from "./mov-ave-spreads.js";
export { listPerfAttrCodes, getPerfAttrAttribution, listPerfAttrThemes, getPerfAttrChart, listPerfAttrStrategyThemes } from "./perf-attribution.js";
export { listIndustrySentimentsThemes, listIndustrySentimentsStrategyThemes, getIndustrySentimentsChart, getIndustrySentimentsChartByCode } from "./industry-sentiments.js";
export { getIndustryCorrelations } from "./industry-correlations.js";
export { getBenchmarkPriceChart, getIndustryAttributionPriceSeries } from "./benchmark-price-chart.js";
export { getAllIndustriesAttribution, getMemberIndexAttribution } from "./industry-attribution-bars.js";
export { getIndustryBenchmarkAttribution, listIndustryAttributionBenchmarks } from "./industry-benchmark-attribution.js";
export { getIndustryEtfPriceSeries, getIndustryEtfContributionBars } from "./industry-etf-contribution.js";
export { getIndustryHypesAndDrains } from "./industry-hypes-and-drains.js";
export { getIntradayMovements, listIntradayMovementsBenchmarks } from "./intraday-movements.js";
export { getIntradayMovementsPrevDayOhlc } from "./intraday-movements-prev-day-ohlc.js";
export {
  listPeAndDividendCodes,
  getPeAndDividendChart,
  listPeAndDividendThemes,
  listPeAndDividendStrategyThemes,
  listPeAndDividendStats,
} from "./pe-and-dividends.js";
export {
  listMarginTrendThemes,
  listMarginTrendStrategyThemes,
  getMarginIndustrySeries,
  getMarginTrends,
} from "./margin-trends.js";
export {
  listFourierFreqsCodes,
  getFourierFreqsChart,
  getFourierFreqsSpectrum,
  listFourierFreqsThemes,
  listFourierFreqsStrategyThemes,
} from "./fourier-freqs.js";
export { getFuturesExt } from "./analysis-futures.service.js";
