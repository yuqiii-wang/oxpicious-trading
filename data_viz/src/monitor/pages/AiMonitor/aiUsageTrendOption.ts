/**
 * Option builder for the AI usage trend chart — stacked per-service
 * TOKEN bars per GMT date (text.llm_requests total_tokens; services on
 * the GROUP_MAJOR_COLORS wheel like the ext-resources trend) + a total
 * request-count line on a SECOND y axis (tokens and requests live on
 * different scales). Clicking a date selects it (vertical markLine —
 * the page drills into that day's service × model breakdown); clicking
 * the selected date again clears it. `dates` / `perService.tokens` /
 * `requests` arrays are index-aligned pivots computed by the page.
 * Pure: option in, option out; theme comes in as `mode` (never read
 * from the store here).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  GROUP_MAJOR_COLORS,
  PALETTE_HI,
} from "@/theme/chart-palette";

export interface UsageSeries {
  service: string;
  /** Total tokens per date, index-aligned with the dates axis. */
  tokens: number[];
}

export function buildAiUsageTrendOption(
  dates: string[],
  perService: UsageSeries[],
  requests: number[],
  selectedDate: string | null,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const series: EChartsOption["series"] = perService.map((s, i) => ({
    name: s.service,
    type: "bar",
    stack: "tokens",
    data: s.tokens,
    barMaxWidth: 28,
    itemStyle: { color: GROUP_MAJOR_COLORS[i % GROUP_MAJOR_COLORS.length] },
  }));
  // The requests line rides a second y axis and carries the
  // selected-date markLine (a markLine belongs to a series but plots
  // independently of its data).
  series.push({
    name: "requests",
    type: "line",
    yAxisIndex: 1,
    data: requests,
    showSymbol: false,
    lineStyle: { color: PALETTE_HI, width: 1.5 },
    itemStyle: { color: PALETTE_HI },
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
    tooltip: commonTooltip(mode),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "tokens",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) =>
            v >= 1_000_000
              ? `${(v / 1_000_000).toFixed(1)}M`
              : v >= 1_000
                ? `${(v / 1_000).toFixed(0)}k`
                : `${v}`,
        },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        name: "requests",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: null,
      },
    ],
    series,
  });
}
