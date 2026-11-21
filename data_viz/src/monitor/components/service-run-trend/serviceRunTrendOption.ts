/**
 * Option builder for the per-service daily run-count trend — one line
 * over the trailing window of Shanghai-local days
 * (monitor.service_audit run starts of ONE service, gap-filled with 0).
 * A selected day draws a dashed vertical markLine (the drill-down panel
 * below lists that day's runs); clicking the selected day again clears
 * it. `dates` / `counts` / `abnormalCounts` are index-aligned arrays
 * from GET /api/monitor/stream/runs/daily. Pure: option in, option out;
 * theme comes in as `mode` (never read from the store here).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  PALETTE_HI,
} from "@/theme/chart-palette";

export function buildServiceRunTrendOption(
  dates: string[],
  counts: number[],
  abnormalCounts: number[],
  selectedDate: string | null,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: null,
    grid: commonGrid({ bottom: 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        const idx = arr[0]?.dataIndex ?? -1;
        if (idx < 0 || idx >= dates.length) return "";
        const runs = counts[idx] ?? 0;
        const abnormal = abnormalCounts[idx] ?? 0;
        return [
          `<b>${dates[idx]}</b>`,
          `${runs} run${runs === 1 ? "" : "s"}`,
          abnormal > 0 ? `${abnormal} abnormal` : null,
        ]
          .filter((s): s is string => s != null)
          .join("<br/>");
      },
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      name: "runs",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      minInterval: 1,
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      {
        name: "runs",
        type: "line",
        data: counts,
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
      },
    ],
  });
}
