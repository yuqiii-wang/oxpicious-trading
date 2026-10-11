/**
 * Option builder for the External Resources trend chart — stacked
 * per-service request bars per date + a total-failed line overlay, with
 * the selected date marked by a vertical line. Pure: option in, option
 * out; theme comes in as `mode` (never read from the store here).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  DOWN_COLOR,
  GROUP_MAJOR_COLORS,
  PALETTE_HI,
} from "@/theme/chart-palette";

export interface TrendSeries {
  service: string;
  /** Requests per date, index-aligned with the dates axis. */
  counts: number[];
}

export function buildExtResourceTrendOption(
  dates: string[],
  perService: TrendSeries[],
  failed: number[],
  selectedDate: string | null,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const series: EChartsOption["series"] = perService.map((s, i) => ({
    name: s.service,
    type: "bar",
    stack: "requests",
    data: s.counts,
    barMaxWidth: 28,
    itemStyle: { color: GROUP_MAJOR_COLORS[i % GROUP_MAJOR_COLORS.length] },
  }));
  // Total-failed overlay + the selected-date markLine ride the trailing
  // line series (a markLine belongs to a series but plots independently
  // of its data).
  series.push({
    name: "failed",
    type: "line",
    data: failed,
    showSymbol: false,
    lineStyle: { color: DOWN_COLOR, width: 1.5 },
    itemStyle: { color: DOWN_COLOR },
    z: 5,
    markLine:
      selectedDate != null
        ? {
            silent: true,
            symbol: "none",
            animation: false,
            lineStyle: { color: PALETTE_HI, type: "dashed", width: 1.2 },
            label: {
              color: c.textColor,
              formatter: selectedDate,
              fontSize: 9,
            },
            data: [{ xAxis: selectedDate }],
          }
        : undefined,
  });
  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      valueFormatter: (v: unknown) => `${v as number} req`,
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series,
  });
}
