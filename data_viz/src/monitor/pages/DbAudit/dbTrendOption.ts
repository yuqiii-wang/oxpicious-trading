/**
 * Option builder for the per-table workload trend chart — write-rate
 * BARS per active pass (colored by the pass's write_flag: ok on the
 * palette wheel, amber orange, red) + a read-rate line on a SECOND y
 * axis (rows/s and scans/s live on different scales). Activity-sparse
 * passes: idle gaps simply have no points. SOURCE-AWARE: archive points
 * (the daily rollup's per-GMT-day series) label the GMT operational day
 * itself — point.date — because a browser-local date of `at` can skew a
 * day off the archive's UTC-day grain. Pure: option in, option out;
 * theme comes in as `mode` (never read from the store here).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { fmtDate } from "@/shared/datetime";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  DOWN_COLOR,
  GROUP_MAJOR_COLORS,
  MA20_COLOR,
  PALETTE_HI,
} from "@/theme/chart-palette";
import type { DbAuditSource, DbTrendPoint } from "@/lib/api-client/monitor";

/** Bar color of one pass by its write_flag code (null/0 ok, 1 amber, 2 red). */
function flagColor(flag: number | null): string {
  if (flag === 2) return DOWN_COLOR;
  if (flag === 1) return MA20_COLOR;
  return GROUP_MAJOR_COLORS[0];
}

export function buildDbTrendOption(
  points: DbTrendPoint[],
  mode: ThemeMode,
  source: DbAuditSource = "live",
): EChartsOption {
  const c = axisColors(mode);
  // browser-local HH:mm per pass (ISO stamps are UTC); archive points
  // label the GMT operational day (full date — ECharts auto-thins the
  // category axis labels when the history runs long)
  const labels = points.map((p) => {
    if (source === "archive" && p.date != null) return p.date;
    const d = new Date(p.at);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  });
  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode),
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      // Archive ticks are GMT-day labels ("2026-11-20") — remapped for
      // display only; live "HH:mm" ticks pass through unchanged.
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => fmtDate(v),
      },
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: null,
      },
    ],
    series: [
      {
        name: "write rows/s",
        type: "bar",
        barMaxWidth: 20,
        data: points.map((p) => ({
          value: p.writeRate,
          itemStyle: { color: flagColor(p.writeFlag) },
        })),
      },
      {
        name: "read scans/s",
        type: "line",
        yAxisIndex: 1,
        data: points.map((p) => p.readRate),
        showSymbol: false,
        lineStyle: { color: PALETTE_HI, width: 1.5 },
        itemStyle: { color: PALETTE_HI },
        z: 5,
      },
    ],
  });
}
