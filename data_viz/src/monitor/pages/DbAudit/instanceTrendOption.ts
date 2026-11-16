/**
 * Option builder for the instance vitals trend — active connections
 * (left axis) + replay backlog in MB (right axis), one line per instance
 * over the trailing window (db_health samples ~1/instance/min). Pure:
 * option in, option out; theme comes in as `mode`.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonGrid,
  GROUP_MAJOR_COLORS,
} from "@/theme/chart-palette";
import type { DbInstanceTrendPoint } from "@/lib/api-client/monitor";

export function buildInstanceTrendOption(
  points: DbInstanceTrendPoint[],
  instanceLabels: Record<number, string>,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  // merged ascending time axis (points arrive per instance, sorted)
  const stamps = [
    ...new Set(points.map((p) => p.at)),
  ].sort();
  const byInstance = new Map<number, Map<string, DbInstanceTrendPoint>>();
  for (const p of points) {
    let m = byInstance.get(p.instance);
    if (!m) {
      m = new Map();
      byInstance.set(p.instance, m);
    }
    m.set(p.at, p);
  }
  const series: EChartsOption["series"] = [];
  for (const [instance, m] of byInstance) {
    const label = instanceLabels[instance] ?? `instance ${instance}`;
    const color = GROUP_MAJOR_COLORS[instance % GROUP_MAJOR_COLORS.length];
    series.push({
      name: `${label} conns`,
      type: "line",
      showSymbol: false,
      lineStyle: { color, width: 1.5 },
      itemStyle: { color },
      data: stamps.map((s) => m.get(s)?.activeConnections ?? null),
    });
    series.push({
      name: `${label} backlog`,
      type: "line",
      yAxisIndex: 1,
      showSymbol: false,
      lineStyle: { color, width: 1.2, type: "dashed" },
      itemStyle: { color },
      data: stamps.map(
        (s) =>
          (m.get(s)?.replayBacklogBytes ?? null) === null
            ? null
            : Number(((m.get(s)!.replayBacklogBytes as number) / 1024 ** 2).toFixed(2)),
      ),
    });
  }
  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 40 }),
    tooltip: commonTooltip(mode),
    xAxis: {
      type: "category",
      data: stamps.map((s) => {
        const d = new Date(s);
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      }),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "connections",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        name: "backlog MB",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: null,
      },
    ],
    series,
  });
}
