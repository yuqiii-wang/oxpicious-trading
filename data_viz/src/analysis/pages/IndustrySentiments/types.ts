/**
 * Shared types for the Industry Sentiments analysis page sub-modules.
 */
import type {
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
} from "../../../../shared/types";
import type { ThemeMode } from "@/store/filters";

/** Pool-size bucket: small <51, mid 51-180, large >180, all = no filter. */
export type PoolSize = "all" | "small" | "mid" | "large";

/** Rolling-correlation window selector. */
export type CorrWindow = "5d" | "20d" | "60d" | "255d";

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
  aggregation: IndustrySentimentsAggRow[];
}

/** Props for the main IndustrySentimentsPlot card. */
export interface PlotProps {
  data: IndustrySentimentsChartResponse;
  themeMode: ThemeMode;
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
  chartDataList: IndustrySentimentsChartResponse[];
  /** Selected industry IDs (passed through from the page so the auto-expanded
   *  Correlation section can fetch pairwise correlation rows from the API).
   *  Empty in single-industry mode. */
  selectedIndustryIds: string[];
}

/** Props for the IndustryBenchmarkAttributionChart component. */
export interface AttributionChartProps {
  industryId: string;
  industryLabel: string;
  /** As-of date for the attribution ("" or null → latest available). */
  date: string | null;
  themeMode: ThemeMode;
  /** The benchmark code selected in the dropdown (shown as the 1st plot).
   *  Highlighted in the attribution bar chart so the user can see where the
   *  navigation benchmark sits relative to the other benchmarks. */
  selectedBenchmarkCode: string | null;
}

/** Props for the BenchmarkPriceChart component (1st plot in attribution mode). */
export interface BenchmarkPriceChartProps {
  /** The benchmark code to fetch + display. */
  benchmarkCode: string | null;
  themeMode: ThemeMode;
  /** Currently selected date (markLine position). Null → latest date. */
  selectedDate: string | null;
  /** Callback fired when the user clicks a date on the chart. */
  onDateSelect: (date: string) => void;
  /** Selected industries to overlay as non-this-industry shades. Each entry
   *  has an industry_id and a display label. When empty, no shades are drawn. */
  selectedIndustries: Array<{ id: string; label: string }>;
}

/** Props for the CorrelationChart component. */
export interface CorrelationChartProps {
  industryIds: string[];
  poolSize: PoolSize;
  themeMode: ThemeMode;
}

/** Props for the IndustryEtfPriceChart component (1st plot in ETF Contribution mode). */
export interface IndustryEtfPriceChartProps {
  /** Selected industry IDs — ETFs tracking member indices of these industries
   *  are fetched and plotted. */
  industryIds: string[];
  themeMode: ThemeMode;
  /** Currently selected date (markLine position). Null → latest date. */
  selectedDate: string | null;
  /** Callback fired when the user clicks a date on the chart. */
  onDateSelect: (date: string) => void;
}

/** Props for the IndustryEtfContributionChart component (2nd+ plots). */
export interface IndustryEtfContributionChartProps {
  industryId: string;
  industryLabel: string;
  /** As-of date for the bars ("" or null → latest available). */
  date: string | null;
  themeMode: ThemeMode;
}

/** Props for the MarketTrendChart component (sole plot in "Market Trend" mode). */
export interface MarketTrendChartProps {
  themeMode: ThemeMode;
}

/** Props for the HypesAndDrainsChart component (sub-view of "Market Trend" mode). */
export interface HypesAndDrainsChartProps {
  /** Selected broad-market benchmark code (e.g. 000300). */
  benchmarkCode: string;
  themeMode: ThemeMode;
}

/** Props for the IndexAllocationView component (the "Index Allocation" mode —
 *  migrated from the standalone "Sec Allocation Perf Attribution" commons
 *  analysis). Reuses the Industry Sentiments classification-nav selection. */
export interface IndexAllocationViewProps {
  themeMode: ThemeMode;
  /** Per-industry chart responses already fetched by the Industry Sentiments
   *  page (one per selected industry, including strategy-only codes fetched
   *  by code). The view resolves its target index set from these. */
  chartDataList: IndustrySentimentsChartResponse[];
  /** L3-selected index codes (empty → use ALL member indices of the selected
   *  industries). */
  selectedItemCodes: string[];
}
