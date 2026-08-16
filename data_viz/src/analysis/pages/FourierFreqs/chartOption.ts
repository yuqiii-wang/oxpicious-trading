/**
 * ECharts option builder for the Fourier Frequencies chart.
 *
 * Renders the dominant cycle PERIOD (freq, trading days) over time, one
 * line per range_days window (20/60/255/500/750). A secondary view of
 * the amplitude is shown in the tooltip.
 *
 * X-axis: last_date (the last trading date of each FFT window).
 * Y-axis: freq (dominant cycle period in trading days) — log scale, because
 *   freq spans 2..range_days and a linear scale would crush the short-cycle
 *   values against the floor.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { RANGE_DAY_SERIES } from "./constants";
import type { FourierFreqsChartRow } from "../../../../shared/types";

/** Per-range_days series data point: [date, freq, amplitude]. */
type FreqPoint = [string, number, number];

/** Pivot flat rows into one series per range_days. */
function pivotByRangeDays(
  rows: FourierFreqsChartRow[],
): Map<number, FreqPoint[]> {
  const map = new Map<number, FreqPoint[]>();
  for (const r of rows) {
    let arr = map.get(r.range_days);
    if (!arr) {
      arr = [];
      map.set(r.range_days, arr);
    }
    arr.push([r.last_date, r.freq, r.amplitude]);
  }
  // Sort each series by date ascending.
  for (const arr of map.values()) {
    arr.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  }
  return map;
}

/** Build the union of all dates across range_days series (sorted). */
function allDates(seriesMap: Map<number, FreqPoint[]>): string[] {
  const set = new Set<string>();
  for (const arr of seriesMap.values()) {
    for (const [d] of arr) set.add(d);
  }
  return Array.from(set).sort();
}

export function buildFourierFreqsOption(
  rows: FourierFreqsChartRow[],
  themeMode: ThemeMode,
  code: string,
  name: string,
): EChartsOption {
  const c = axisColors(themeMode);
  const seriesMap = pivotByRangeDays(rows);
  const dates = allDates(seriesMap);

  // Build per-range_days series. Each series aligns its points to the
  // union date axis via a value-index lookup (missing dates → null).
  const indexByDate = new Map<string, number>();
  dates.forEach((d, i) => indexByDate.set(d, i));

  const series = RANGE_DAY_SERIES.map((spec) => {
    const pts = seriesMap.get(spec.range_days) ?? [];
    const data: (number | null)[] = new Array(dates.length).fill(null);
    for (const [d, freq] of pts) {
      const idx = indexByDate.get(d);
      if (idx != null) data[idx] = freq;
    }
    return {
      name: spec.label,
      type: "line" as const,
      data,
      smooth: false,
      showSymbol: false,
      lineStyle: { width: 1.5, color: spec.color },
      itemStyle: { color: spec.color },
      // Stash amplitude per point for the tooltip (indexed by date).
      // ECharts doesn't natively carry extra dims on a 1-D series, so we
      // store amplitude in a side channel keyed by (range_days, date).
    };
  });

  // Side channel: amplitude lookup for the tooltip.
  const ampByKey = new Map<string, number>();
  for (const [rd, arr] of seriesMap) {
    for (const [d, , amp] of arr) {
      ampByKey.set(`${rd}|${d}`, amp);
    }
  }

  const title = name ? `${code} · ${name}` : code;

  return {
    backgroundColor: "transparent",
    title: {
      text: title,
      left: 8,
      top: 4,
      textStyle: {
        fontSize: 12,
        fontWeight: 600,
        color: c.textColor,
      },
    },
    legend: commonLegend(themeMode),
    grid: commonGrid({ top: 48, bottom: 48 }),
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      axisPointer: { type: "line", lineStyle: { color: c.axisLineColor } },
      formatter: (params: unknown) => {
        const arr = params as Array<{
          axisValueLabel?: string;
          seriesName?: string;
          color?: string;
          dataIndex?: number;
          value?: number | null;
        }>;
        if (!Array.isArray(arr) || arr.length === 0) return "";
        const dateLabel = arr[0].axisValueLabel ?? "";
        const lines: string[] = [`<b>${dateLabel}</b>`];
        for (const p of arr) {
          const rdSpec = RANGE_DAY_SERIES.find((s) => s.label === p.seriesName);
          if (!rdSpec) continue;
          const freq = p.value;
          const amp = ampByKey.get(`${rdSpec.range_days}|${dateLabel}`);
          const freqStr = freq != null ? `${freq}d` : "—";
          const ampStr = amp != null ? `· amp ${fmtNum(amp, 2)}` : "";
          lines.push(
            `<span style="color:${p.color ?? ""}">●</span> ${p.seriesName}: <b>${freqStr}</b> ${ampStr}`,
          );
        }
        return lines.join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        hideOverlap: true,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "log",
      name: "Cycle (days)",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      min: 2,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        formatter: (v: number) => String(Math.round(v)),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed" } },
    },
    dataZoom: commonDataZoom(),
    series,
  };
}
