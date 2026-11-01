/**
 * Shared types for the Market Beta analysis page sub-modules.
 */
import type {
  MarketBetaAggRow,
  MarketBetaChartResponse,
} from "@shared/types";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import type { InsightPageIdentity } from "@/shared/ai-ask/insightPages";
import type { ECharts, EChartsOption } from "echarts";

/** Pool-size bucket: small <51, mid 51-180, large >180, all = no filter. */
export type PoolSize = "all" | "small" | "mid" | "large";

/** Correlation window selector — MA-W curves correlated over the W trading
 *  days starting on start_date (window starts every `interval` trading
 *  days on the calendar grid). */
export type CorrWindow = "20d" | "60d" | "255d";

/** Rolling-days selector for the BenchmarkPriceChart shade overlay.
 *  Each value picks one of the 6 pre-materialized
 *  benchmark_non_this_industry_rolling_{N}days_price columns from
 *  analysis.industry_attributions. The dropdown lets the user pick which
 *  trailing window (5 / 20 / 60 / 120 / 255 / 500 trading days) drives the
 *  non-this-industry shade. 120d (~6 months) is the default. */
export type RollingDays = 5 | 20 | 60 | 120 | 255 | 500;

/**
 * One industry's precomputed aggregation set, used to render a per-industry
 * mean curve in multi-industry "Mean only" mode.
 */
export interface PerIndustryAggregation {
  industry_id: string;
  industry_label: string;
  aggregation: MarketBetaAggRow[];
}

/** Props for the main MarketBetaPlot card. */
export interface PlotProps {
  data: MarketBetaChartResponse;
  /** When true, the data is a merge of multiple industries. The single
   *  mean/var overlay is hidden; instead, when meanOnly is ON, one mean
   *  curve per industry is rendered (each in a distinct color). */
  multiIndustry: boolean;
  /** Number of source industries in the merge (1 when single-select). Used
   *  only for the subtitle when multiIndustry is true. */
  numIndustries: number;
  /** Per-industry chart responses (the un-merged source data). Used to build
   *  per-industry aggregation sets for the multi-industry mean overlay. Empty
   *  in single-industry mode (the merged `data.aggregation` is used instead). */
  chartDataList: MarketBetaChartResponse[];
  /** Effective industry IDs driving the correlation plot (selected industries
   *  + strategy themes + industries derived from L3 codes — the same set the
   *  merged chart fetches). The always-visible Correlation section fetches
   *  pairwise correlation rows from the API for these. */
  selectedIndustryIds: string[];
  /** L3-selected member index codes (empty when no L3 chip is selected).
   *  Passed through to the Correlation section's refresh button (the corr
   *  runner resolves codes → industry_ids and unions them with
   *  selectedIndustryIds). */
  selectedItemCodes: string[];
}

/** Props for the IndustryBenchmarkAttributionChart component. */
export interface AttributionChartProps extends BaseChartProps {
  industryId: string;
  industryLabel: string;
  /** As-of date for the attribution ("" or null → latest available). */
  date: string | null;
  /** The benchmark code selected in the dropdown (shown as the 1st plot).
   *  Highlighted in the attribution bar chart so the user can see where the
   *  navigation benchmark sits relative to the other benchmarks. */
  selectedBenchmarkCode: string | null;
}

/** Props for the BenchmarkPriceChart component (1st plot in attribution mode). */
export interface BenchmarkPriceChartProps extends BaseChartProps {
  /** The benchmark code to fetch + display. */
  benchmarkCode: string | null;
  /** Currently selected date (markLine position). Null → latest date. */
  selectedDate: string | null;
  /** Callback fired when the user clicks a date on the chart. */
  onDateSelect: (date: string) => void;
  /** Selected industries to overlay as non-this-industry shades. Each entry
   *  has an industry_id and a display label. When empty, no shades are drawn. */
  selectedIndustries: Array<{ id: string; label: string }>;
  /** Fired when a benchmark price load settles — the FULL row span (null
   *  for a cleared/empty load). The Market Sentiments pages bridges the
   *  date-event strip's axis with this until the first visible-range
   *  report lands. Stable identity (useCallback) keeps the fetch effect
   *  free of stale-closure churn. */
  onLoaded?: (data: { rows: Array<{ date: string }> } | null) => void;
  /** Fired whenever the chart's VISIBLE date window changes — dataZoom
   *  slider/inside drags, and every data rebuild (which re-anchors the
   *  window). Carries { start, end } date strings of the visible range
   *  (null when there are no rows). The Market Sentiments pages windows
   *  the date-event strip to the same slider with this (the strip has NO
   *  slider of its own there). Stable identity (useCallback) avoids
   *  needless re-binds. */
  onVisibleRangeChange?: (range: { start: string; end: string } | null) => void;
  /** Center the dataZoom window on this date (keeping the current window
   *  span) — the Market Sentiments pages's date-event strip click jumps
   *  the benchmark chart's slider to the event day. Pass a fresh
   *  { date, seq } per request (the same seq is never re-applied);
   *  non-trading dates snap forward to the next trading day. */
  focusDateRequest?: { date: string; seq: number } | null;
  /** The Ask AI insight page the chart's ask modal resolves. Defaults to
   *  the benchmark-price registry page (the chart's own numerics); a host
   *  embedding the chart in another analysis context overrides it — the
   *  Market Sentiments page swaps in its keyword-audit page. */
  insightPage?: InsightPageIdentity;
}

/** Props for the CorrelationChart component. */
export interface CorrelationChartProps extends BaseChartProps {
  industryIds: string[];
  /** L3-selected member index codes (empty → only industryIds drive the
   *  refresh). Passed to the corr runner's --code arg; codes are resolved
   *  to industry_ids Python-side and unioned with industryIds. */
  codes: string[];
  poolSize: PoolSize;
}

/** Props for the IndustryEtfPriceChart component (1st plot in ETF Contribution mode). */
export interface IndustryEtfPriceChartProps extends BaseChartProps {
  /** Selected industry IDs — ETFs tracking member indices of these industries
   *  are fetched and plotted. */
  industryIds: string[];
  /** Currently selected date (markLine position). Null → latest date. */
  selectedDate: string | null;
  /** Callback fired when the user clicks a date on the chart. */
  onDateSelect: (date: string) => void;
}

/** Props for the IndustryEtfContributionChart component (2nd+ plots). */
export interface IndustryEtfContributionChartProps extends BaseChartProps {
  industryId: string;
  industryLabel: string;
  /** As-of date for the bars ("" or null → latest available). */
  date: string | null;
}

/** Weighting method of the Hypes & Drains ranking: 'equal' (raw attribution
 *  contribution) or 'amt' (contribution × shared trading amount). */
export type HypesWeighting = "equal" | "amt";

/** The Hypes & Drains sub-view's control state — owned by the parent card
 *  (its Ask AI spec must describe the CURRENT view) and rendered by the
 *  child chart. */
export interface HypesControls {
  periodDays: number;
  weighting: HypesWeighting;
  maxRank: 3 | 5;
}

/** Props for the HypesAndDrainsChart component (sub-view of "Market Trend" mode). */
export interface HypesAndDrainsChartProps {
  /** Selected broad-market benchmark code (e.g. 000300). */
  benchmarkCode: string;
  /** Lifted control state (period / weighting / max rank). */
  controls: HypesControls;
  /** Whole-object control updater (the child patches and sends back). */
  onControlsChange: (controls: HypesControls) => void;
  /** ECharts instance forwarder — the parent card's Ask AI addon screenshots
   *  this chart's canvas (the chart body is a bare BaseChart here). */
  onReady?: (chart: ECharts) => void;
  /** Built-option forwarder — feeds the parent card's Ask AI plot info. */
  onOptionChange?: (option: EChartsOption | null) => void;
}

/** Props for the IndexAllocationView component (the "Index Allocation" mode —
 *  migrated from the standalone "Sec Allocation Perf Attribution" commons
 *  analysis). Reuses the Market Beta classification-nav selection. */
export interface IndexAllocationViewProps {
  /** Per-industry chart responses already fetched by the Market Beta
   *  page (one per selected industry, including strategy-only codes fetched
   *  by code). The view resolves its target index set from these. */
  chartDataList: MarketBetaChartResponse[];
  /** L3-selected index codes (empty → use ALL member indices of the selected
   *  industries). */
  selectedItemCodes: string[];
}
