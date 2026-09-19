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

// Shared fetch transport (retry + per-endpoint timeout). Every fetchJson
// caller already goes through it — these exports are for surfacing the
// resulting errors in UI alerts or tuning a specific call.
export {
  formatFetchError,
  HttpError,
  DEFAULT_TIMEOUT_MS,
  resolveTimeoutMs,
  type FetchRetryOptions,
} from "./_retry";

export {
  fetchDebtBaseline,
  fetchPbocOmaAnnouncements,
} from "./debt";

export {
  fetchNewsThemes,
  fetchNewsStrategyThemes,
  fetchNewsAuthors,
  fetchNewsCalendar,
  fetchNewsItems,
  fetchNewsItem,
  fetchNewsComments,
  runNewsSearch,
  fetchNewsTokenize,
} from "./news";

export {
  fetchAiCalendar,
  fetchAiItems,
  fetchAiQaDetail,
  type AiScopeParams,
} from "./ai";

export {
  askChartAi,
  type AiAskRequest,
  type AiAskResponse,
} from "./aiAsk";

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

export { fetchSecBoardMap } from "./sec-board";

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
} from "./analysis-ma-spread";

export { fetchAnalysisForecast, fetchForecastTriggerDates, fetchForecastIdentity } from "./analysis-forecasts";

export { fetchMarketHypes } from "./analysis-market-hypes";

export {
  fetchPeAndDividendCodes,
  fetchPeAndDividendThemes,
  fetchPeAndDividendStrategyThemes,
  fetchPeAndDividendChart,
  fetchPeAndDividendStats,
  fetchPeAndDividendStreaks,
} from "./analysis-pe-and-dividend";

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
  fetchTradingSignalHistory,
  type TradingSignalsRunResponse,
  type TradingSignalConfig,
  type TradingSignalConfigsResponse,
  type TradingSignal,
  type TradingSignalsResponse,
  type TradingSignalHistoryResponse,
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
