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
  fetchOptionsWalls,
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
  fetchQuarterlyComposition,
  fetchIndustryWeightSeries,
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
  fetchFuturesProducts,
  fetchFuturesCombined,
} from "./futures";

export {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
  fetchMovAveSpreadChart,
  fetchMovAveSpreadForecast,
} from "./analysis-ma-spread";

export {
  fetchPeAndDividendCodes,
  fetchPeAndDividendThemes,
  fetchPeAndDividendStrategyThemes,
  fetchPeAndDividendChart,
  fetchPeAndDividendStats,
  fetchPeAndDividendStreaks,
} from "./analysis-pe-and-dividend";

export {
  fetchRecurringCyclesThemes,
  fetchRecurringCyclesStrategyThemes,
  fetchRecurringCyclesChart,
  fetchRecurringCyclesSpectrum,
} from "./analysis-recurring-cycles";

export {
  fetchMarginTrendThemes,
  fetchMarginTrendStrategyThemes,
  fetchMarginIndustrySeries,
  fetchMarginTrends,
} from "./analysis-margin-trends";

export { fetchFuturesExt } from "./analysis-futures";

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
  runIndustryCorrelationsRefresh,
  INDUSTRY_CORR_RUN_TAG,
} from "./analysis-industry-sentiments";

export {
  fetchIndustryCorrOffsets,
  fetchIndustryCorrOffsetBenchmarks,
  fetchIndustryCorrOffsetIndustries,
  runIndustryCorrOffsetsRefresh,
  INDUSTRY_CORR_OFFSET_RUN_TAG,
} from "./analysis-corr-offsets";

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
  fetchIntradayMovementsDates,
  fetchIntradayMovements,
  fetchIntradayMovementsPrevDayOhlc,
} from "./intraday-movements";

export {
  runSecAllocLivePipeline,
  fetchSecAllocLiveAttribution,
  fetchSecAllocLiveRunStatus,
  SEC_ALLOC_LIVE_REF_TAG,
  SEC_ALLOC_LIVE_REF_DL_TAG,
  SEC_ALLOC_LIVE_REF_BASE_TAG,
  SEC_ALLOC_LIVE_LIVE_TAG,
  type SecAllocLiveRunResponse,
} from "./sec-alloc-live";

export {
  runTradingSignals,
  runTradingSignalsAnalysis,
  fetchTradingSignalsRunStatus,
  fetchTradingSignalConfigs,
  fetchTradingSignals,
  type TradingSignalsRunResponse,
  type TradingSignalConfig,
  type TradingSignalConfigsResponse,
  type TradingSignal,
  type TradingSignalsResponse,
} from "./trading-signals";

export {
  runAnalysisForSecurity,
  fetchAnalysisRunStatus,
  analysisRunTag,
  type RunnableAnalysisModule,
  type AnalysisRunResponse,
} from "./analysis-run";

export type {
  StrategyAlgo,
  StrategySelection,
  RunStrategyResult,
  CheckExistingResult,
  TrainConfigRow,
  TrainRunRow,
  TrainLossType,
  TrainTrialRow,
  TrainInfoResponse,
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
  checkExistingStrategy,
  runSingletonStrategy,
  trainStrategyModel,
  fetchTrainInfo,
  fetchStrategyProcessStatus,
  singletonRunTag,
  singletonTrainTag,
} from "./strategy";
