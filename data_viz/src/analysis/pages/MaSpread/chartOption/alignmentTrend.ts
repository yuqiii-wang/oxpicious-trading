/**
 * Alignment trajectory option (the "alignment" extras group) — the
 * five-leg MA-chain alignment score (0..5) as a step line, with the
 * score's verdict zones shaded by state: 5 = bullish 多头排列 (top band),
 * 0 = bearish 空头排列 (bottom band), 1..4 = mixed 纠缠 (the middle).
 * The y axis is the discrete score — min/max pinned so the bands frame
 * the full range on every code.
 *
 * NO slider of its own: the chart's visible window is the panel's one
 * synced zoomRange state (see ../types.ChartZoomRange) — the main plot's
 * slider drives it and this chart's inside zoom follows (either chart's
 * pan/zoom feeds back through the same state). Its rows share the main
 * plot's exact date axis (both read the same full-window detail rows), so
 * the index-based window + hover sync (shared/charts/tooltipSync) is
 * exact.
 *
 * The hover tooltip carries the per-date VERDICT (多头排列 / 纠缠 /
 * 空头排列, colored bull/bear) + the x/5 score — the decoded
 * alignment_state from the same rows (NULL score rows show "insufficient
 * history", never a verdict). The section's latest-day verdict chip was
 * folded into this tooltip (2026-11-14).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  UP_COLOR,
  DOWN_COLOR,
} from "@/theme/chart-palette";
import type { ChartZoomRange } from "../types";
import type { MaAlignment, MovAveSpreadAlignmentDay } from "@shared/types";

/** The ma_alignment verdict's label + tooltip color per decoded state. */
const VERDICT: Record<MaAlignment, { label: string; color?: string }> = {
  bullish: { label: "多头排列", color: UP_COLOR },
  mixed: { label: "纠缠" },
  bearish: { label: "空头排列", color: DOWN_COLOR },
};

/** ECharts areaPatcher-safe hexes per theme (muted, palette-derived). */
function bandColors(mode: ThemeMode) {
  return mode === "dark"
    ? { bull: "rgba(67,160,71,0.18)", bear: "rgba(229,57,53,0.15)" }
    : { bull: "rgba(67,160,71,0.14)", bear: "rgba(229,57,53,0.12)" };
}

export function buildAlignmentTrendOption(
  rows: MovAveSpreadAlignmentDay[],
  mode: ThemeMode,
  zoom?: ChartZoomRange | null,
): EChartsOption {
  const c = axisColors(mode);
  const bands = bandColors(mode);
  const dates = rows.map((r) => r.date);
  const scores = rows.map((r) => r.alignment_score);
  return baseChartOption(mode, {
    grid: commonGrid({ left: 40, right: 16, bottom: 24, top: 18 }),
    // Inside zoom only — no slider: the shared zoomRange state (the main
    // plot's slider) drives this window.
    dataZoom: commonDataZoom(undefined, zoom?.start, zoom?.end).filter(
      (dz) => dz.type === "inside",
    ),
    legend: null,
    tooltip: commonTooltip(mode, {
      formatter: (params: unknown) => {
        const p = (
          Array.isArray(params) ? params[0] : params
        ) as { dataIndex?: number; axisValueLabel?: string; marker?: string };
        const row = p.dataIndex != null ? rows[p.dataIndex] : undefined;
        if (!row) return "";
        const score = row.alignment_score;
        const value =
          typeof score === "number"
            ? `${
                row.alignment_state != null
                  ? (() => {
                      const v = VERDICT[row.alignment_state];
                      const label =
                        v.color != null
                          ? `<span style="color:${v.color};font-weight:600">${v.label}</span>`
                          : v.label;
                      return `${label} · `;
                    })()
                  : ""
              }${score}/5 legs`
            : "— (insufficient history)";
        return `${p.axisValueLabel ?? row.date}<br/>${p.marker ?? ""}alignment ${value}`;
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
