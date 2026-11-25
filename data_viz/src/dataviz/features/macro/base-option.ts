/**
 * Shared option builder for the Macro panels. Both panels share the same
 * x-axis (date), zoom-aware tick labels — the tick granularity comes in
 * from useDateTickGranularity: month / quarter / year, whichever is densest
 * for the visible time-slider window at the measured width, so the labels
 * never overlap (see shared/charts/dateTicks) — and a connected group for
 * cross-chart tooltip sync (the debt page's sharex behaviour). The option
 * preamble (background / animation / legend / tooltip / grid) comes from
 * the shared base-chart kit; the date x-axis and the CNY-left /
 * USD-right twin y-axes stay family-specific.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { axisColors, commonDataZoom, commonLegend, commonGrid } from "@/theme/chart-palette";
import { baseChartOption, commonTooltip, type BaseChartOptionOverrides } from "@/shared/charts/base-chart";
import { dateTickFilter, type DateTickGranularity } from "@/shared/charts/dateTicks";
import { fmtDate } from "@/shared/datetime";

export type AxisColors = ReturnType<typeof axisColors>;

/** Delegate to the shared axis-color resolver (single source of truth). */
export function getAxisColors(mode: ThemeMode): AxisColors {
  return axisColors(mode);
}

/**
 * Build the common x-axis + twin-y + grid + tooltip base option shared by
 * both macro panels. SGE (onshore CNY) rides the left axis, the COMEX
 * (offshore USD) series the right one — the two quote units never share a
 * scale.
 */
export function buildBaseOption(
  dates: string[],
  mode: ThemeMode,
  /** Tick granularity from useDateTickGranularity — month / quarter / year
   *  for the visible time-slider window at the measured chart width. */
  tickGranularity: DateTickGranularity,
  extra: BaseChartOptionOverrides = {},
): EChartsOption {
  const c = getAxisColors(mode);
  const dateTicks = dateTickFilter(dates, tickGranularity);

  return baseChartOption(mode, {
    grid: commonGrid({
      left: 64,
      right: 64,
      top: 28,
      bottom: 56,
    }),
    tooltip: commonTooltip(mode),
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { alignWithLabel: true },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        interval: (_idx: number, value: string) => dateTicks.has(value),
        formatter: (v: string) => fmtDate(v),
        rotate: 0,
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "CNY (SGE)",
        nameLocation: "middle",
        nameGap: 50,
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: {
          lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.5 },
        },
      },
      {
        type: "value",
        scale: true,
        name: "USD (COMEX)",
        nameLocation: "middle",
        nameGap: 50,
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    legend: commonLegend(mode, { left: "right", itemWidth: 14, itemHeight: 8 }),
    dataZoom: commonDataZoom(),
    ...extra,
  });
}
