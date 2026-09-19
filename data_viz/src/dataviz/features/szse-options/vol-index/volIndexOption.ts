/**
 * Vol index chart builder — one line per underlying for the daily 30-day
 * model-free implied-volatility index (analysis.options_vol_index,
 * computed by analyze/options/compute/vix.py with the CBOE VIX
 * methodology on settlement prices; see docs/options_vol_smile_study.md).
 *
 * Series are aligned on the sorted union of dates (null where an
 * underlying has no value that day — honest gaps, no connecting).
 */
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import {
  MUTED_PALETTE,
  UNDERLYING_LABELS,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { ThemeMode } from "@/store/filters";
import type { VolIndexRow } from "@shared/types";
import type { EChartsOption } from "echarts";

export function volIndexUnderlyingLabel(code: string): string {
  return UNDERLYING_LABELS[code] ?? code;
}

/** Colored series dot inside tooltip rows (repo ColoredDot convention). */
function tooltipDot(color: string): string {
  return (
    `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;` +
    `background:${color};margin-right:5px;border:1px solid rgba(0,0,0,0.2);` +
    `vertical-align:middle;"></span>`
  );
}

export function buildVolIndexOption(
  rows: VolIndexRow[],
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  const byUnderlying = new Map<string, Map<string, number | null>>();
  const dateSet = new Set<string>();
  for (const r of rows) {
    let series = byUnderlying.get(r.underlying_code);
    if (!series) {
      series = new Map();
      byUnderlying.set(r.underlying_code, series);
    }
    series.set(r.date, r.vol_index_30d);
    dateSet.add(r.date);
  }
  const dates = Array.from(dateSet).sort();
  const codes = Array.from(byUnderlying.keys()).sort();

  return baseChartOption(themeMode, {
    title: {
      text: "Vol Index (vol pts) — smile LEVEL per underlying",
      left: 4,
      top: 0,
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: commonTooltip(themeMode, {
      // The original tooltip block had no axisPointer — keep the default
      // line pointer (dropping the kit's cross/snap default).
      axisPointer: undefined,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: c.textColor, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{
          dataIndex: number;
          seriesName: string;
          value: number | null;
          color?: string;
        }>;
        const idx = list[0]?.dataIndex ?? -1;
        const date = idx >= 0 && idx < dates.length ? dates[idx] : "";
        const lines = list
          .filter((p) => p.value != null)
          .map((p) => {
            const dot = p.color != null ? tooltipDot(p.color) : "";
            return (
              `<div style="line-height:1.7;">${dot}${p.seriesName}: ` +
              `<b>${fmtNum(p.value as number, 2)}</b></div>`
            );
          })
          .join("");
        return (
          `<div style="font-weight:600;border-bottom:1px solid ${c.splitLineColor};` +
          `margin-bottom:4px;padding-bottom:2px;">${date}</div>` +
          (lines || "<div>—</div>")
        );
      },
    }),
    legend: commonLegend(themeMode, {
      data: codes.map(volIndexUnderlyingLabel),
    }),
    dataZoom: commonDataZoom(),
    grid: commonGrid({ top: 40, bottom: 44 }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      name: "vol pts",
      scale: true,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: codes.map((code, i) => {
      const series = byUnderlying.get(code)!;
      return {
        name: volIndexUnderlyingLabel(code),
        type: "line" as const,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length], width: 1.4 },
        itemStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length] },
        emphasis: { focus: "series" as const },
        data: dates.map((d) => series.get(d) ?? null),
      };
    }),
  });
}
