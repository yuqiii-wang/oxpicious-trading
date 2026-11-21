/**
 * Shared ECharts option preamble — the fragment every option builder used to
 * re-declare by hand: transparent background, animations off (the data
 * refreshes in place; transitions would fight the notMerge rebuilds), the
 * themed legend / tooltip / grid fragments, on top of `axisColors`.
 *
 * Builders spread their chart-specific keys (series, axes, title, dataZoom)
 * through `overrides`; a fragment key set to `null` is omitted entirely (e.g. a
 * single-series chart with no legend, an item-trigger scatter with its own
 * tooltip).
 */
import type {
  EChartsOption,
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonGrid,
  commonLegend,
  commonTooltip,
} from "@/theme/chart-palette";

/**
 * Per-key override contract for `baseChartOption`. Everything from
 * `EChartsOption` except the three fragment keys, which are additionally
 * nullable — `null` means "omit the default fragment".
 */
export interface BaseChartOptionOverrides
  extends Partial<Omit<EChartsOption, "legend" | "tooltip" | "grid">> {
  legend?: LegendComponentOption | null;
  tooltip?: TooltipComponentOption | null;
  /** Single grid or an array (multi-grid stacked panels). */
  grid?: GridComponentOption | GridComponentOption[] | null;
}

/**
 * Merge a partial `legend`/`tooltip` override over its themed common fragment,
 * so a builder passing `legend: { data: [...] }` inherits the unified legend
 * theming (`legendColors(mode)`) instead of falling back to ECharts' hard
 * defaults — `#333` label text, which is invisible on the dark background
 * (the Volatility page's legends were the reported case). This is what keeps
 * the legend/tooltip color scheme unified app-wide: builders tune layout keys
 * only and the colors ride in from the shared fragments.
 *
 * The `textStyle` sub-object merges one level deeper, so an override that only
 * tunes `fontSize` keeps the themed text color. `undefined` → base untouched;
 * `null` is handled by the caller (omit the fragment).
 */
function mergeFragment<T extends object>(base: T, override: T | null | undefined): T {
  if (override == null) return base;
  const merged: T = { ...base, ...override };
  const baseStyle = (base as { textStyle?: object }).textStyle;
  const overrideStyle = (override as { textStyle?: object }).textStyle;
  if (baseStyle && overrideStyle) {
    (merged as { textStyle?: object }).textStyle = { ...baseStyle, ...overrideStyle };
  }
  return merged;
}

/**
 * Build an option with the shared chart preamble. The three common fragments
 * default to `commonLegend(mode)` / `commonTooltip(mode)` / `commonGrid()`;
 * partial `legend`/`tooltip` overrides MERGE over the themed fragments
 * (`null` drops a fragment entirely), `grid` replaces verbatim (build its
 * partials through `commonGrid(overrides)`); every other key is spread
 * verbatim after the preamble.
 */
export function baseChartOption(
  mode: ThemeMode,
  overrides: BaseChartOptionOverrides = {},
): EChartsOption {
  const {
    legend: legendOverride,
    tooltip: tooltipOverride,
    grid = commonGrid(),
    ...rest
  } = overrides;
  const option: EChartsOption = {
    backgroundColor: "transparent",
    animation: false,
    legend: mergeFragment(commonLegend(mode), legendOverride),
    tooltip: mergeFragment(commonTooltip(mode), tooltipOverride),
    grid,
    ...rest,
  };
  if (legendOverride === null) delete option.legend;
  if (tooltipOverride === null) delete option.tooltip;
  if (grid === null) delete option.grid;
  return option;
}

/**
 * Centered "[No data]"-style placeholder option — the empty-state pattern
 * used by charts that render an option in every state (data still loading,
 * no rows for the selected date, ...).
 */
export function emptyChartOption(mode: ThemeMode, message: string): EChartsOption {
  const c = axisColors(mode);
  return {
    backgroundColor: "transparent",
    title: {
      text: message,
      left: "center",
      top: "center",
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 400 },
    },
  };
}
