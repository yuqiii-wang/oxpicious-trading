/**
 * pseudoImageaChartOption — the ROUGH chart reconstruction option for a
 * Pseudo Imagea chart block: the comprehension's critical points only
 * (no attempt at the original series styling). kind bar|table|mixed →
 * bar, line → line, pie → pie; multi-series points group by `series`.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { axisColors, commonGrid, MUTED_PALETTE } from "@/theme/chart-palette";

export interface PseudoImageChartPoint {
  label: string;
  value: number;
  series?: string | null;
}

export interface PseudoImageChart {
  kind: string; // line | bar | pie | table | mixed
  title?: string | null;
  summary: string;
  points: PseudoImageChartPoint[];
}

const SERIES_FALLBACK = "数值";

export function buildPseudoImageaChartOption(
  chart: PseudoImageChart,
  mode: ThemeMode,
): EChartsOption {
  const axis = axisColors(mode);
  if (chart.kind === "pie") {
    return baseChartOption(mode, {
      legend: null,
      grid: null,
      tooltip: commonTooltip(mode, { trigger: "item", axisPointer: undefined }),
      series: [
        {
          type: "pie",
          radius: "74%",
          center: ["50%", "52%"],
          data: chart.points.map((p, i) => ({
            name: p.series ? `${p.series}·${p.label}` : p.label,
            value: p.value,
            itemStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length] },
          })),
        },
      ],
    });
  }
  // bar (bar | table | mixed) or line — category axis over the points'
  // labels (reading order), one series per distinct `series` value.
  const type = chart.kind === "line" ? "line" : "bar";
  const labels: string[] = [];
  for (const p of chart.points) {
    if (!labels.includes(p.label)) labels.push(p.label);
  }
  const seriesNames: string[] = [];
  for (const p of chart.points) {
    const name = p.series || SERIES_FALLBACK;
    if (!seriesNames.includes(name)) seriesNames.push(name);
  }
  const series = seriesNames.map((name, i) => ({
    name,
    type: type as "bar" | "line",
    barMaxWidth: 24,
    data: labels.map((label) => {
      const point = chart.points.find(
        (p) => p.label === label && (p.series || SERIES_FALLBACK) === name,
      );
      return point ? point.value : null;
    }),
    itemStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length] },
  }));
  return baseChartOption(mode, {
    legend:
      seriesNames.length > 1
        ? { data: seriesNames, textStyle: { fontSize: 9 }, top: 0 }
        : null,
    grid: commonGrid({
      top: seriesNames.length > 1 ? 24 : 12,
      bottom: 30,
      left: 8,
      right: 8,
      containLabel: true,
    }),
    tooltip:
      type === "line"
        ? commonTooltip(mode)
        : commonTooltip(mode, { trigger: "item", axisPointer: undefined }),
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: axis.axisLineColor } },
      axisLabel: { color: axis.textColor, fontSize: 9, interval: 0 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: axis.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: axis.splitLineColor } },
    },
    series,
  });
}
