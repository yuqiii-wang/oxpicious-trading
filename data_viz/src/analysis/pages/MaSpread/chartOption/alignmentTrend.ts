/**
 * Alignment trajectory option (the "alignment" extras group) — the
 * five-leg MA-chain alignment score (0..5) as a step line, with the
 * score's verdict zones shaded by state: 5 = bullish 多头排列 (top band),
 * 0 = bearish 空头排列 (bottom band), 1..4 = mixed 纠缠 (the middle).
 * The y axis is the discrete score — min/max pinned so the bands frame
 * the full range on every code.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { axisColors, commonDataZoom, commonGrid } from "@/theme/chart-palette";
import type { MovAveSpreadAlignmentDay } from "@shared/types";

/** ECharts areaPatcher-safe hexes per theme (muted, palette-derived). */
function bandColors(mode: ThemeMode) {
  return mode === "dark"
    ? { bull: "rgba(67,160,71,0.18)", bear: "rgba(229,57,53,0.15)" }
    : { bull: "rgba(67,160,71,0.14)", bear: "rgba(229,57,53,0.12)" };
}

export function buildAlignmentTrendOption(
  rows: MovAveSpreadAlignmentDay[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const bands = bandColors(mode);
  const dates = rows.map((r) => r.date);
  const scores = rows.map((r) => r.alignment_score);
  return baseChartOption(mode, {
    grid: commonGrid({ left: 40, right: 16, bottom: 50, top: 18 }),
    dataZoom: commonDataZoom(),
    legend: null,
    tooltip: commonTooltip(mode, {
      valueFormatter: (v: unknown) =>
        typeof v === "number" ? `${v}/5 legs` : "— (insufficient history)",
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      min: -0.4,
      max: 5.4,
      interval: 1,
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => (v < 0 || v > 5 ? "" : String(v)),
      },
    },
    series: [
      {
        name: "alignment",
        type: "line",
        step: "middle",
        connectNulls: false,
        data: scores,
        lineStyle: { width: 1.4, color: "#2196F3" },
        itemStyle: { color: "#2196F3" },
        symbolSize: 3,
        markArea: {
          silent: true,
          data: [
            [
              { yAxis: 4.5, itemStyle: { color: bands.bull } },
              { yAxis: 5.4 },
            ],
            [
              { yAxis: -0.4, itemStyle: { color: bands.bear } },
              { yAxis: 0.5 },
            ],
          ],
        },
      },
    ],
  });
}
