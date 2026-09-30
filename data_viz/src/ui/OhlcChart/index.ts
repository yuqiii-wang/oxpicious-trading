/**
 * OhlcChart — the base daily-OHLC chart stack.
 *
 *   • OhlcChart     the shared daily OHLC chart for one stock
 *                        (candles / MA / PE / margin / amount + dividend,
 *                        trade-signal and forecast-trigger overlays)
 *   • CodeTrendChart     the class-based per-security trend card that
 *                        fetches (sec_type, code) rows and renders
 *                        OhlcChart inside card chrome; extend its
 *                        protected hooks for custom behaviour
 *
 * Single import source for the base layer — import both components and
 * their types from here.
 */
export { default as OhlcChart } from "./OhlcChart";
export type {
  OhlcChartProps,
  OhlcTradeSignal,
  OhlcForecastAction,
  HighlightSpan,
} from "./types";
export { default as CodeTrendChart } from "./CodeTrendChart";
export type {
  CodeTrendSecType,
  CodeTrendRow,
  CodeTrendData,
  CodeTrendSource,
  CodeTrendChartOptions,
  CodeTrendChartProps,
} from "./codeTrendTypes";
export {
  CODE_TREND_SOURCES,
  stripCodeSuffix,
  mapEtfRows,
  mapIndexRows,
} from "./codeTrendSources";
