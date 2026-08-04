/**
 * Shared constants for the Industry Sentiments analysis page sub-modules.
 */
import type { PoolSize, CorrWindow } from "./types";

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
 * mode. ColorBrewer Set1 — high contrast and colorblind-friendly. Each
 * industry's mean line + ±1σ band reuses the same color so the user can
 * visually pair a mean curve with its dispersion band.
 */
export const MEAN_PALETTE = [
  "#e41a1c", // red
  "#377eb8", // blue
  "#4daf4a", // green
  "#984ea3", // purple
  "#ff7f00", // orange
  "#a65628", // brown
  "#f781bf", // pink
  "#999999", // grey
];

/** Colors per pool_size slice (used by the aggregate charts). */
export const POOL_COLORS: Record<PoolSize, string> = {
  all: "#424242",
  small: "#1976d2",
  mid: "#f57c00",
  large: "#388e3c",
};

/** Available correlation-window selector values. */
export const CORR_WINDOWS: CorrWindow[] = ["5d", "20d", "60d", "255d"];
