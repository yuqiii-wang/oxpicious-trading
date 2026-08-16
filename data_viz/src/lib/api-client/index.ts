/**
 * Barrel re-export of the API client sub-modules.
 *
 * Consumers should keep importing from `@/lib/api-client` — this file
 * preserves the public surface of the original monolithic api-client.ts.
 */
export {
  clearApiCache,
  invalidateCacheForUrl,
  invalidateCacheForPrefix,
} from "./_cache";

export {
  fetchDebtBaseline,
  fetchPbocOmaAnnouncements,
} from "./debt";

export {
  fetchUnderlyings,
  fetchOptionsCombined,
  fetchEtfOhlcv,
} from "./options";

export {
  fetchThemes,
  fetchEtfStrategyThemes,
  fetchEtfMarginCombined,
} from "./etf-margin";

export {
  fetchIndexList,
  fetchIndexThemes,
  fetchIndexStrategyThemes,
  fetchIndicesCombined,
  fetchIndexIntraday5min,
} from "./index-baseline";

export {
  fetchSecComposition,
  fetchLinkedEtfs,
  fetchSimilarIndices,
} from "./sec-composition";

export {
  fetchStockBaseline,
  fetchStockThemes,
  fetchStockStrategyThemes,
  fetchStocksCombined,
} from "./stock-baseline";

export {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
  fetchMovAveSpreadChart,
} from "./analysis-ma-spread";

export {
  fetchPeAndDividendCodes,
  fetchPeAndDividendThemes,
  fetchPeAndDividendStrategyThemes,
  fetchPeAndDividendChart,
  fetchPeAndDividendStats,
} from "./analysis-pe-and-dividend";

export {
  fetchFourierFreqsCodes,
  fetchFourierFreqsThemes,
  fetchFourierFreqsStrategyThemes,
  fetchFourierFreqsChart,
  fetchFourierFreqsSpectrum,
} from "./analysis-fourier-freqs";

export {
  fetchMarginTrendThemes,
  fetchMarginTrendStrategyThemes,
  fetchMarginIndustrySeries,
  fetchMarginIndustryCorrelation,
  fetchMarginTrends,
} from "./analysis-margin-trends";

export {
  fetchPerfAttrCodes,
  fetchPerfAttrThemes,
  fetchPerfAttrStrategyThemes,
  fetchPerfAttrAttribution,
  fetchPerfAttrChart,
} from "./analysis-perf-attr";

export {
  fetchIndustrySentimentsThemes,
  fetchIndustrySentimentsStrategyThemes,
  fetchIndustrySentimentsChart,
  fetchIndustrySentimentsChartByCode,
  fetchIndustryCorrelations,
} from "./analysis-industry-sentiments";

export {
  fetchIndustryBenchmarkAttribution,
  fetchIndustryAttributionBenchmarks,
  fetchBenchmarkPriceChart,
  fetchIndustryAttributionPriceSeries,
  fetchAllIndustriesAttribution,
  fetchIndustryHypesAndDrains,
  fetchMemberIndexAttribution,
} from "./analysis-industry-attribution";

export {
  fetchIndustryEtfPriceSeries,
  fetchIndustryEtfContributionBars,
} from "./analysis-industry-etf";

export {
  fetchLiveDataDates,
  fetchLiveDataThemes,
  fetchLiveDataStrategyThemes,
  fetchLiveDataCombined,
} from "./live-data";

export {
  fetchIntradayMovementsBenchmarks,
  fetchIntradayMovements,
} from "./intraday-movements";

export type {
  StrategyAlgo,
  StrategySelection,
  ForecastScenarioResponse,
  RunStrategyResult,
} from "./strategy";

export {
  STRATEGY_ALGOS,
  DEFAULT_STRATEGY_SELECTION,
  ALGO_LABELS,
  ftSuffix,
  selectionToStrategyName,
  serializeSelection,
  isBinarySelection,
  selectionSum,
  selectionLabel,
  fetchSingletonBacktest,
  fetchSingletonRisks,
  fetchSingletonForecast1m,
  fetchForecastScenarioDecisions,
  runSingletonStrategy,
} from "./strategy";
