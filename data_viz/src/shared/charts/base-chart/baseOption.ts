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
import { fmtDate, fmtMonth, isDateLike } from "@/shared/datetime/display";

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
 * Thousands separators — mirrors echarts' `addCommas` (its default numeric
 * axis/pointer label text), so numeric axis-pointer bubbles that pass
 * through our date formatter render exactly as before.
 */
function addCommas(n: number): string {
  const parts = String(n).split(".");
  return parts[0].replace(/(\d{1,3})(?=(?:\d{3})+(?!\d))/g, "$1,")
    + (parts.length > 1 ? `.${parts[1]}` : "");
}

/**
 * Axis-pointer label formatter — the seam echarts' DEFAULT axis-trigger
 * tooltip runs its header (`axisValueLabel`) through, alongside the
 * cross-pointer value bubbles. Date categories render in the UI-wide
 * yyyy-Mmm-dd display format; every other value renders exactly as
 * echarts' built-in default (category strings verbatim, numbers with
 * commas, time-axis ticks as dates).
 */
function axisPointerDateLabel(params: { value: unknown; axisDimension?: string }): string {
  const v = params.value;
  if (typeof v === "string") {
    // Category axis: date strings remap; other categories (code names, ...)
    // pass through untouched.
    return isDateLike(v) ? (v.length === 7 ? fmtMonth(v) : fmtDate(v)) : v;
  }
  if (typeof v === "number") {
    // Only the x (time) axis carries epoch-millis values (all datasets
    // post-2001, so ≥1e12 ms); numeric axes (rates, prices, amounts —
    // incl. y-axis values beyond 1e12 yuan) keep the built-in commas.
    if (params.axisDimension === "x" && v >= 1e12) return fmtDate(v);
    return addCommas(v);
  }
  return fmtDate(v as Date);
}

/**
 * Inject the date-header formatter into a default axis tooltip: builders
 * that pass their own `formatter` (or their own axisPointer label
 * formatter) keep full control; item-trigger tooltips have no axis header.
 * The formatter only changes the DATE-LIKE values, so the default tooltip
 * layout, value lines and `valueFormatter` handling stay echarts' own.
 */
function applyDateHeaderFormatter(tooltip: TooltipComponentOption): TooltipComponentOption {
  if (!tooltip || tooltip.trigger !== "axis" || tooltip.formatter != null) {
    return tooltip;
  }
  const ap = (tooltip.axisPointer ?? {}) as Record<string, unknown>;
  const label = (ap.label ?? {}) as Record<string, unknown>;
  if (label.formatter != null) return tooltip;
  return {
    ...tooltip,
    axisPointer: { ...ap, label: { ...label, formatter: axisPointerDateLabel } },
  };
}

/**
 * Build an option with the shared chart preamble. The three common fragments
 * default to `commonLegend(mode)` / `commonTooltip(mode)` / `commonGrid()`;
 * partial `legend`/`tooltip` overrides MERGE over the themed fragments
 * (`null` drops a fragment entirely), `grid` replaces verbatim (build its
 * partials through `commonGrid(overrides)`); every other key is spread
 * verbatim after the preamble. Default axis tooltips get the UI-wide
 * yyyy-Mmm-dd date-header formatting (see `applyDateHeaderFormatter`).
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
    tooltip: applyDateHeaderFormatter(
      mergeFragment(commonTooltip(mode), tooltipOverride),
    ),
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
