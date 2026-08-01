/**
 * Shared option builder for the Debt Baseline panels. All four panels share
 * the same x-axis (date), quarterly axis labels, and a connected group for
 * cross-chart tooltip sync — matching the matplotlib sharex=True behaviour
 * in plot_debt_baseline.py.
 */
import type { EChartsOption, SeriesOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { axisColors, MUTED_PALETTE, commonLegend, commonGrid } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";

export type AxisColors = ReturnType<typeof axisColors>;

/** Delegate to the shared axis-color resolver (single source of truth). */
export function getAxisColors(mode: ThemeMode): AxisColors {
  return axisColors(mode);
}

/**
 * Compute quarterly tick positions (first business date of each quarter).
 * Mirrors format_date_axis_quarterly in _plot_commons.py.
 */
export function quarterTickFilter(dates: string[]): Set<string> {
  const out = new Set<string>();
  let lastQuarter = -1;
  for (const d of dates) {
    const m = Number(d.slice(5, 7));
    const q = Math.floor((m - 1) / 3);
    if (q !== lastQuarter) {
      out.add(d);
      lastQuarter = q;
    }
  }
  return out;
}

/**
 * Build the common x-axis + grid + tooltip + dataset base option shared by
 * all four debt-baseline panels.
 *
 * If `markerMap` is provided, PBoC operation info (outright repo / MLF) is
 * shown in the tooltip on hover instead of dense vertical markLines.
 */
export function buildBaseOption(
  dates: string[],
  mode: ThemeMode,
  extra: Partial<EChartsOption> = {},
  markerMap?: Map<string, string[]>,
): EChartsOption {
  const c = getAxisColors(mode);
  const quarterTicks = quarterTickFilter(dates);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({
      left: 64,
      right: 64,
      top: 28,
      bottom: 56,
    }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | Array<number | string>;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let header = `<div style="font-weight:600;margin-bottom:4px">${dateStr}</div>`;
        // Show PBoC operation info on hover instead of dense markLines
        const markerInfo = markerMap?.get(dateStr);
        if (markerInfo && markerInfo.length > 0) {
          header += `<div style="font-size:10px;margin-bottom:4px;color:${MUTED_PALETTE[1]}">${markerInfo.join("<br/>")}</div>`;
        }
        const body = arr
          .filter((p) => {
            if (p.value == null) return false;
            if (Array.isArray(p.value)) return p.value.length > 0;
            return !Number.isNaN(p.value as number);
          })
          .map((p) => {
            const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
            const vstr = typeof v === "number" ? fmtNum(v) : String(v ?? "");
            return `<div>${p.marker ?? ""} ${p.seriesName ?? ""}: <b>${vstr}</b></div>`;
          })
          .join("");
        return header + body;
      },
    },
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
        interval: (_idx: number, value: string) => quarterTicks.has(value),
        formatter: (v: string) => v.slice(0, 10),
        rotate: 0,
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.5 } },
      },
      {
        type: "value",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    legend: commonLegend(mode, { left: "right", itemWidth: 14, itemHeight: 8 }),
    ...extra,
  };
}

/**
 * Build markLine series entries for outright-repo + MLF announcement dates.
 * Same colour convention as plot_debt_baseline.py: orange = MLF, red = outright.
 *
 * Each markLine entry is a single-element array `[{xAxis: date}]` which ECharts
 * interprets as one vertical line at that x value.
 */
export function buildMarkerMarkLines(
  markers: Array<{ date: string; type: "outright_repo" | "MLF" }>,
): SeriesOption[] {
  if (markers.length === 0) return [];
  const MLF_COLOR = MUTED_PALETTE[1]; // muted orange
  const OUTRIGHT_COLOR = MUTED_PALETTE[3]; // muted red
  const mlfData = markers
    .filter((m) => m.type === "MLF")
    .map((m) => ({ xAxis: m.date }));
  const outrightData = markers
    .filter((m) => m.type === "outright_repo")
    .map((m) => ({ xAxis: m.date }));

  const series: SeriesOption[] = [];
  if (mlfData.length > 0) {
    series.push({
      type: "line",
      name: "MLF date",
      data: [],
      markLine: {
        symbol: ["none", "none"],
        silent: true,
        animation: false,
        lineStyle: { color: MLF_COLOR, type: "dashed", width: 1, opacity: 0.55 },
        data: mlfData,
      },
    });
  }
  if (outrightData.length > 0) {
    series.push({
      type: "line",
      name: "Outright repo date",
      data: [],
      markLine: {
        symbol: ["none", "none"],
        silent: true,
        animation: false,
        lineStyle: { color: OUTRIGHT_COLOR, type: "dashed", width: 1, opacity: 0.55 },
        data: outrightData,
      },
    });
  }
  return series;
}
