/**
 * codeTrendTypes — the public type surface of CodeTrendChart (props,
 * data shapes, per-sec_type source contract). Split out of
 * CodeTrendChart.tsx so sources/header/ai-ask modules can consume the
 * shapes without importing the component class.
 */
import type React from "react";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  IndustrySentimentsIndex,
  MarketRegimeSpans,
  SecBoardTag,
  StockDailyRow,
  StockDividend,
} from "@shared/types";
import type OhlcChart from "./OhlcChart";

/** Security type the chart knows how to fetch a trend for. */
export type CodeTrendSecType = "etf" | "index" | "stock";

/** Normalized per-day row shape the base chart consumes (StockDailyRow). */
export type CodeTrendRow = StockDailyRow;

/** Industry-mode payload (`industryId` prop): the industry's member-index
 *  close series — mapped from GET /api/analysis/industry-sentiments/chart.
 *  Closes stay RAW (no rebasing); the chart plots one close-price line per
 *  member index on the union date axis. */
export interface CodeTrendIndustryData {
  industry_id: string;
  industry_label: string;
  /** One entry per member index with index_basic_stats rows (the API omits
   *  indices with no close data). */
  indices: IndustrySentimentsIndex[];
}

/** Result of one code-trend fetch, already mapped to CodeTrendRow[]. */
export interface CodeTrendData {
  rows: CodeTrendRow[];
  /** Display name as reported by the API (null when the API has none). */
  name: string | null;
  /** Dividend events (stock only) — drawn as ex-div date markers. */
  dividends: StockDividend[];
  /** Industry mode only (`industryId` prop): member-index close series.
   *  `rows` is empty in that mode — the industry lines render instead of
   *  the OHLC chart. */
  industry?: CodeTrendIndustryData | null;
}

/** One sec_type's data source: fetch a code's daily rows + map them. */
export interface CodeTrendSource {
  fetch: (code: string) => Promise<CodeTrendData>;
}

/**
 * Pass-through options for the inner OhlcChart — everything except the
 * pieces this component owns (rows / ohlcMode / height). This keeps full
 * compatibility with the shared chart's option surface: dataZoom windowing,
 * dividend markers, date-click callbacks, etc.
 */
export type CodeTrendChartOptions = Partial<
  Omit<React.ComponentProps<typeof OhlcChart>, "rows" | "ohlcMode" | "height">
>;

export interface CodeTrendChartProps {
  /** Security type — selects the baseline endpoint + row mapping. */
  secType: CodeTrendSecType;
  /** Security code — suffixed ("000001.SZ") or bare ("000001"). Ignored
   *  when `industryId` is set (industry mode). */
  code: string;
  /** INDUSTRY MODE — when set, plot the industry instead of one security:
   *  one RAW close-price line per member index of this industry (fetched
   *  from /api/analysis/industry-sentiments/chart). The OHLC mode toggle,
   *  market-regime shading, board tags and the AI Ask button are
   *  suppressed; windowing is the in-chart dataZoom slider. */
  industryId?: string | null;
  /** Display name (e.g. from a nav tree); falls back to the API's name. */
  name?: string;
  /** Content height in px (chart gets height − 60, slider fits below). */
  height?: number;
  /** Initial OHLC display mode. Defaults to "percentage" (% change rebased). */
  defaultOhlcMode?: OhlcMode;
  /** Show the Absolute / %-change toggle in the header. Default true. */
  showModeToggle?: boolean;
  /** Show the MUI date-range slider below the chart. Default false — the
   *  default windowing is the in-chart ECharts dataZoom slider (shared
   *  commonDataZoom, same style as the MA-Spread chart), which needs the
   *  FULL unwindowed row history to work. */
  showRangeSlider?: boolean;
  /** "card" (default) wraps in Card chrome with header; "bare" renders just
   *  chart + slider for embedding under an existing header. */
  variant?: "card" | "bare";
  /** Header title override (defaults to "code · name"). */
  title?: React.ReactNode;
  /** Extra text appended to the default subtitle (e.g. "3.21% holding"). */
  subtitleExtra?: string;
  /** Extra header control rendered after the mode toggle (close button, …). */
  headerAction?: React.ReactNode;
  /** Per-sec_type source overrides (merged over CODE_TREND_SOURCES). */
  sources?: Partial<Record<CodeTrendSecType, CodeTrendSource>>;
  /** Extra online-search seed keywords for the AI Ask "?" — the page's
   *  items of interest that live OUTSIDE this chart (e.g. the forecast
   *  family buttons beneath it: RSI, Bollinger, …). Merged ahead of the
   *  auto-derived instrument/series keywords; identity-stable (memoized)
   *  like every other chart input. */
  aiSearchKeywords?: string[];
  /** Inner-chart option pass-through (dividends, dataZoom, onDateClick, …). */
  chartOptions?: CodeTrendChartOptions;
  /** Fired after each load settles (success or empty; not on error). */
  onLoaded?: (data: CodeTrendData | null) => void;
  /** Fired when the user switches the OHLC display mode. */
  onOhlcModeChange?: (mode: OhlcMode) => void;
}

export interface CodeTrendChartState {
  data: CodeTrendData | null;
  loading: boolean;
  error: string | null;
  /** OHLC display mode — "percentage" rebases OHLC + MAs to % change from
   *  the first valid close; "absolute" shows raw prices. */
  ohlcMode: OhlcMode;
  /** Date-range slider window (row indices) — [0, len-1] = full history. */
  range: [number, number];
  /** Market-regime SPAN shading toggle (stats.market_regimes) — the
   *  per-regime bands (hot purple / panic red / quiet blue) beside the
   *  Absolute/% Change toggle. Off by default; the first enable fetches
   *  the code's spans once (GET /api/analysis/market-regimes) and caches
   *  them per code. */
  showRegimes: boolean;
  regimeSpans: Partial<MarketRegimeSpans> | null;
  regimesLoading: boolean;
  /** Board tag(s) beside the header title (stats.sec_board_map via
   *  GET /api/sec-board): a stock's single listing board, or an
   *  ETF/index's composition-weighted board mix. Fetched in parallel
   *  with the trend rows; empty when no data / fetch failure. */
  boardTags: SecBoardTag[];
}
