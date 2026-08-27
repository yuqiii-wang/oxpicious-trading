/**
 * Shared constants for the Industry Sentiments analysis page sub-modules.
 */
import type { PoolSize, CorrWindow, RollingDays } from "./types";
import { GROUP_MAJOR_COLORS } from "@/theme/chart-palette";

/**
 * ECharts group name for cross-chart tooltip + axisPointer sync. All three
 * main plots (price, PE, trading amount) share the same date axis
 * (`visibleDates`), so hovering any one dispatches a `updateaxispointer`
 * event to the others — a vertical crosshair line + tooltip appear at the
 * same date on every connected chart. The CorrelationChart is excluded
 * because it has its OWN date axis (different categories).
 */
export const CHART_GROUP = "industry-sentiments";

/**
 * Stable color per benchmark code — used for the line, the dropdown
 * checkbox indicator, and the tooltip ━ marker so each benchmark is
 * visually consistent across the UI.
 */
export const BENCHMARK_COLORS: Record<string, string> = {
  "000300": "#ff6b35", // 沪深300 — orange
  "000016": "#1565c0", // 上证50 — blue
  "000852": "#6a1b9a", // 中证1000 — purple
  "932000": "#c2185b", // 中证2000 — deep pink
  "000688": "#00897b", // 科创50 — teal
};

/**
 * Distinct colors for per-industry mean curves in multi-industry "Mean only"
 * mode. Alias of the shared `GROUP_MAJOR_COLORS` (ColorBrewer Set1 — high
 * contrast and colorblind-friendly) so per-industry mean lines use the SAME
 * major color as that industry's member-index variants on the price chart.
 * Each industry's mean line + ±1σ band reuses the same color so the user can
 * visually pair a mean curve with its dispersion band.
 */
export const MEAN_PALETTE = GROUP_MAJOR_COLORS;

/** Colors per pool_size slice (used by the aggregate charts). */
export const POOL_COLORS: Record<PoolSize, string> = {
  all: "#424242",
  small: "#1976d2",
  mid: "#f57c00",
  large: "#388e3c",
};

/** Available correlation-window selector values. Each W correlates the two
 *  industries' MA-W curves over the W trading days starting on start_date. */
export const CORR_WINDOWS: CorrWindow[] = ["20d", "60d", "255d"];

/** Available rolling-days selector values for the BenchmarkPriceChart shade
 *  overlay. Each value N corresponds to the pre-materialized column
 *  benchmark_non_this_industry_rolling_{N}days_price in
 *  analysis.industry_attributions. 120d (~6 months) is the default. */
export const ROLLING_DAYS: RollingDays[] = [5, 20, 60, 120, 255, 500];

/** Default rolling-days window for the BenchmarkPriceChart shade overlay.
 *  120 trading days ≈ 6 months — the default for the shade overlay AND for
 *  analysis.industry_hypes_and_drains. */
export const DEFAULT_ROLLING_DAYS: RollingDays = 120;

/** Human-readable label for each rolling-days option (shown in the dropdown). */
export const ROLLING_DAYS_LABELS: Record<RollingDays, string> = {
  5: "5 days",
  20: "20 days",
  60: "60 days",
  120: "120 days (~6m)",
  255: "255 days (~1y)",
  500: "500 days (~2y)",
};

/** Map a RollingDays value to the corresponding row field name on
 *  IndustryAttributionPriceSeriesRow. Used to look up the selected
 *  column's values without a switch statement. */
export const ROLLING_DAYS_FIELD: Record<RollingDays, string> = {
  5: "non_this_industry_rolling_5days_price",
  20: "non_this_industry_rolling_20days_price",
  60: "non_this_industry_rolling_60days_price",
  120: "non_this_industry_rolling_120days_price",
  255: "non_this_industry_rolling_255days_price",
  500: "non_this_industry_rolling_500days_price",
};

/**
 * The four broad-market indices plotted in "Market Trend" mode. Each is
 * fetched as a daily close series (via the benchmark-price endpoint) and
 * rebased to 100 at the start of the visible (zoom) window so the four
 * curves are comparable on a common scale regardless of absolute level.
 *
 * 399001 (深证成指) is used in place of 399106 (深证综指) because 399106's
 * price data has not been downloaded yet.
 */
export const MARKET_TREND_INDICES: Array<{ code: string; name: string; color: string }> = [
  { code: "000001", name: "上证指数", color: "#d32f2f" }, // red
  { code: "399001", name: "深证成指", color: "#1565c0" }, // blue
  { code: "399006", name: "创业板指", color: "#2e7d32" }, // green
  { code: "000680", name: "科创综指", color: "#6a1b9a" }, // purple
];
