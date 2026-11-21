/**
 * The close-price overlay shared by the four vol option builders — the
 * raw session close on its own right-hand price axis (price level, not
 * annualized), drawn FIRST in each series array so the vol curves stay
 * on top. PALETTE_HI is the page's established close-line blue (the
 * rebased-close token) — blue reads as "price" here just like
 * everywhere else in the app, and the muted-blue palette slot stays
 * free for the vol curves on every chart.
 */
import type { LineSeriesOption } from "echarts/charts";
import { axisColors, PALETTE_HI } from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { VolatilityRow } from "@shared/types";

/** The one close color everywhere on the page (blue, no vol curve uses
 *  blue on any of the four charts). */
export const CLOSE_COLOR = PALETTE_HI;

/** The close line series (a thin context line behind the vol curves);
 *  `yAxisIndex` points at the chart's price axis. The per-series tooltip
 *  formatter keeps price points plain while the axis-level formatter
 *  stays percent-shaped for the vol series. */
export function buildCloseSeries(
  rows: VolatilityRow[],
  yAxisIndex: number,
): LineSeriesOption {
  return {
    type: "line",
    name: "Close",
    yAxisIndex,
    data: rows.map((r) => r.close),
    showSymbol: false,
    lineStyle: { width: 1.5, color: CLOSE_COLOR, opacity: 0.9 },
    itemStyle: { color: CLOSE_COLOR },
    emphasis: { focus: "series" },
    tooltip: {
      valueFormatter: (v) => (v == null ? "—" : Number(v).toFixed(2)),
    },
  };
}

/** The right-hand price axis the close line rides; `offset` shifts it
 *  further right when the chart already has a right-hand axis (the
 *  regime z-score, the decomposition jump ratio). No axis name — the
 *  Close legend entry names the line, and a name would collide with
 *  the scroll legend in the top-right corner. */
export function closeAxis(mode: ThemeMode, offset = 0) {
  const c = axisColors(mode);
  return {
    type: "value",
    position: "right" as const,
    ...(offset > 0 ? { offset } : {}),
    scale: true,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { show: false },
    axisLabel: { color: c.textColor, fontSize: 9 },
  };
}
