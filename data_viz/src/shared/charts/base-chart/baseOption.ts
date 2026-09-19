/**
 * Shared ECharts option preamble — the fragment every option builder used to
 * re-declare by hand: transparent background, animations off (the data
 * refreshes in place; transitions would fight the notMerge rebuilds), the
 * themed legend / tooltip / grid fragments, on top of `axisColors`.
 *
 * Builders spread their chart-specific keys (series, axes, title, dataZoom)
 * through `overrides`; a fragment set to `null` is omitted entirely (e.g. a
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
 * Build an option with the shared chart preamble. The three common fragments
 * default to `commonLegend(mode)` / `commonTooltip(mode)` / `commonGrid()`
 * and can be replaced per key or dropped with `null`; every other key is
 * spread verbatim after the preamble.
 */
export function baseChartOption(
  mode: ThemeMode,
  overrides: BaseChartOptionOverrides = {},
): EChartsOption {
  const {
    legend = commonLegend(mode),
    tooltip = commonTooltip(mode),
    grid = commonGrid(),
    ...rest
  } = overrides;
  const option: EChartsOption = {
    backgroundColor: "transparent",
    animation: false,
    legend,
    tooltip,
    grid,
    ...rest,
  };
  if (legend === null) delete option.legend;
  if (tooltip === null) delete option.tooltip;
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
